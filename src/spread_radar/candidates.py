from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
from collections.abc import Mapping
from datetime import UTC, datetime
from math import ceil
from time import monotonic
from typing import Any
from urllib.parse import urlencode

import httpx

from .errors import UpstreamApiError
from .models import TokenCandidate

GECKO_BASE_URL = "https://api.geckoterminal.com/api/v2"
OKX_HOT_TOKEN_URL = "https://web3.okx.com/api/v6/dex/market/token/hot-token"
GECKO_PUBLIC_CALLS_PER_MINUTE = 10
GECKO_INTER_REQUEST_SECONDS = 60 / GECKO_PUBLIC_CALLS_PER_MINUTE
_gecko_rate_lock = asyncio.Lock()
_gecko_last_request_at = 0.0

# These are high-volume base assets, not the speculative targets the radar is
# meant to find.  The list is deliberately conservative and user-editable in
# code if a strategy wants to include one of them.
EXCLUDED_SYMBOLS = {
    "USDT", "USDC", "USDE", "DAI", "FDUSD", "USDS", "PYUSD", "BUSD",
    "WBNB", "BNB", "WETH", "ETH", "WBTC", "BTC", "SOL",
}


def build_okx_auth_headers(
    access_key: str,
    secret_key: str,
    passphrase: str,
    method: str,
    path: str,
    params: Mapping[str, str] | None = None,
    body: str = "",
    *,
    timestamp: str | None = None,
) -> dict[str, str]:
    """Build the standard, per-request OKX REST authentication headers.

    Secrets are read only from the process environment by the application
    entry points.  This helper never logs them or places them in URLs.
    """

    query = urlencode(params or {})
    request_path = f"{path}?{query}" if query else path
    request_timestamp = timestamp or datetime.now(UTC).isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")
    prehash = f"{request_timestamp}{method.upper()}{request_path}{body}"
    signature = base64.b64encode(
        hmac.new(secret_key.encode(), prehash.encode(), hashlib.sha256).digest()
    ).decode()
    return {
        "OK-ACCESS-KEY": access_key,
        "OK-ACCESS-SIGN": signature,
        "OK-ACCESS-TIMESTAMP": request_timestamp,
        "OK-ACCESS-PASSPHRASE": passphrase,
    }


def _number(value: object) -> float:
    try:
        return float(value) if value is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def parse_geckoterminal_payload(
    payload: Mapping[str, Any], network: str, limit: int
) -> list[TokenCandidate]:
    """Parse and de-duplicate the most liquid pool for each base token."""

    tokens = {
        item["id"]: item.get("attributes", {})
        for item in payload.get("included", [])
        if item.get("type") == "token" and item.get("id")
    }
    selected: dict[str, TokenCandidate] = {}

    for pool in payload.get("data", []):
        attributes = pool.get("attributes", {})
        base_ref = (
            pool.get("relationships", {})
            .get("base_token", {})
            .get("data", {})
            .get("id")
        )
        token = tokens.get(base_ref, {})
        symbol = str(token.get("symbol", "")).upper().strip()
        address = str(token.get("address", "")).strip()
        price = _number(attributes.get("base_token_price_usd"))
        volume = _number(attributes.get("volume_usd", {}).get("h24"))
        liquidity = _number(attributes.get("reserve_in_usd"))
        if not symbol or not address or symbol in EXCLUDED_SYMBOLS:
            continue
        if price <= 0 or volume <= 0 or liquidity <= 0:
            continue

        candidate = TokenCandidate(
            symbol=symbol,
            address=address,
            network=network,
            onchain_price_usd=price,
            volume_24h_usd=volume,
            liquidity_usd=liquidity,
            source="geckoterminal",
        )
        existing = selected.get(address.lower())
        if existing is None or candidate.volume_24h_usd > existing.volume_24h_usd:
            selected[address.lower()] = candidate

    return sorted(
        selected.values(), key=lambda item: item.volume_24h_usd, reverse=True
    )[:limit]


class GeckoTerminalCandidateSource:
    """Free fallback for a ranked high-volume DEX candidate list.

    The tweet used OKX's paid endpoint.  GeckoTerminal is included so that the
    scanner remains usable without granting the program any payment authority.
    """

    async def fetch(self, network: str, limit: int) -> list[TokenCandidate]:
        url = f"{GECKO_BASE_URL}/networks/{network}/pools"
        async with httpx.AsyncClient(timeout=20.0) as client:
            # GeckoTerminal returns 20 pools per page. Its public API has an
            # approximate 10-call/minute limit, so pages are deliberately
            # serialized instead of burst concurrently.
            responses = []
            for page in range(1, ceil(limit / 20) + 1):
                responses.append(
                    await _gecko_get(
                        client,
                        url,
                        {"page": str(page), "include": "base_token"},
                    )
                )
        for response in responses:
            if response.status_code != 200:
                raise UpstreamApiError(
                    f"GeckoTerminal returned HTTP {response.status_code}: {response.text[:300]}"
                )
        combined = {"data": [], "included": []}
        for response in responses:
            page = response.json()
            combined["data"].extend(page.get("data", []))
            combined["included"].extend(page.get("included", []))
        return parse_geckoterminal_payload(combined, network, limit)


async def _gecko_get(
    client: httpx.AsyncClient, url: str, params: dict[str, str]
) -> httpx.Response:
    """Enforce the public rate limit across scans, not merely within one scan."""

    global _gecko_last_request_at
    async with _gecko_rate_lock:
        remaining = GECKO_INTER_REQUEST_SECONDS - (monotonic() - _gecko_last_request_at)
        if remaining > 0:
            await asyncio.sleep(remaining)
        response = await client.get(url, params=params)
        _gecko_last_request_at = monotonic()
        return response


class OkxHotTokenCandidateSource:
    """The exact OKX hot-token candidate source referenced in the tweet.

    OKX gives API-key holders a monthly included quota for this Basic endpoint.
    Credentials are signed on the local machine for each request.  The tool
    never receives a wallet private key or signs an x402 payment.
    """

    def __init__(
        self,
        x402_payment: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        passphrase: str | None = None,
    ) -> None:
        self._x402_payment = x402_payment
        self._access_key = access_key
        self._secret_key = secret_key
        self._passphrase = passphrase

    async def fetch(self, network: str, limit: int) -> list[TokenCandidate]:
        params = {
            "rankingType": "4",
            "chainIndex": network,
            "rankingTimeFrame": "2",  # one hour
            "rankBy": "5",  # trade volume (USD)
            "limit": str(min(limit, 100)),
            "riskFilter": "true",
        }
        supplied_credentials = (
            self._access_key,
            self._secret_key,
            self._passphrase,
        )
        if any(supplied_credentials) and not all(supplied_credentials):
            raise UpstreamApiError(
                "OKX authentication is incomplete. Set OKX_ACCESS_KEY, "
                "OKX_SECRET_KEY and OKX_PASSPHRASE together."
            )
        headers = (
            build_okx_auth_headers(
                self._access_key,
                self._secret_key,
                self._passphrase,
                "GET",
                "/api/v6/dex/market/token/hot-token",
                params,
            )
            if all(supplied_credentials)
            else {}
        )
        if self._x402_payment:
            headers["X-PAYMENT"] = self._x402_payment
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(OKX_HOT_TOKEN_URL, params=params, headers=headers)
        if response.status_code == 402:
            if all(supplied_credentials):
                raise UpstreamApiError(
                    "OKX Hot Token returned HTTP 402 although local OKX API "
                    "credentials were sent. Check that the API key is active, "
                    "has Market Data access, and that its Basic monthly quota "
                    "is still available. Do not provide a wallet private key."
                )
            raise UpstreamApiError(
                "OKX Hot Token returned HTTP 402 because this server has no "
                "local OKX API credentials. Create .env from .env.example, set "
                "OKX_ACCESS_KEY, OKX_SECRET_KEY and OKX_PASSPHRASE, then restart "
                "the dashboard. The scanner never accepts a wallet private key."
            )
        if response.status_code != 200:
            raise UpstreamApiError(
                f"OKX Hot Token returned HTTP {response.status_code}: {response.text[:300]}"
            )

        body = response.json()
        if body.get("code") not in ("0", 0, None):
            raise UpstreamApiError(f"OKX Hot Token error: {body.get('msg', body)}")

        candidates: list[TokenCandidate] = []
        for token in body.get("data", []):
            symbol = str(token.get("tokenSymbol", "")).upper().strip()
            address = str(token.get("tokenContractAddress", "")).strip()
            price = _number(token.get("price"))
            volume = _number(token.get("volume"))
            liquidity = _number(token.get("liquidity"))
            if not symbol or not address or symbol in EXCLUDED_SYMBOLS:
                continue
            if price <= 0 or volume <= 0:
                continue
            candidates.append(
                TokenCandidate(
                    symbol=symbol,
                    address=address,
                    network=network,
                    onchain_price_usd=price,
                    volume_24h_usd=volume,
                    liquidity_usd=liquidity,
                    source="okx-hot-token",
                )
            )
        return candidates[:limit]
