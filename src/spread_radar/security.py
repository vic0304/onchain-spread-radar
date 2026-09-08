"""Per-contract tax and trading-risk checks for read-only candidate screening."""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass, replace
from time import monotonic
from typing import Any

import httpx

from .models import TokenCandidate

GOPLUS_TOKEN_SECURITY_URL = "https://api.gopluslabs.io/api/v1/token_security"
GOPLUS_NETWORK_IDS = {
    "eth": "1",
    "1": "1",
    "optimism": "10",
    "10": "10",
    "bsc": "56",
    "56": "56",
    "polygon_pos": "137",
    "137": "137",
    "base": "8453",
    "8453": "8453",
    "arbitrum": "42161",
    "42161": "42161",
}
GOPLUS_MIN_REQUEST_INTERVAL_SECONDS = 0.65
TAX_CACHE_SECONDS = 10 * 60
_rate_lock = asyncio.Lock()
_last_request_at = 0.0
_profile_cache: dict[tuple[str, str], tuple[float, "TaxProfile"]] = {}


@dataclass(frozen=True, slots=True)
class TaxProfile:
    buy_tax_bps: float | None
    sell_tax_bps: float | None
    risk_reason: str | None = None


def _as_bps(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        tax = float(str(value))
    except (TypeError, ValueError):
        return None
    if not 0 <= tax <= 1:
        return None
    return tax * 10_000


def _is_true(payload: dict[str, Any], field: str) -> bool:
    return str(payload.get(field, "")).strip() == "1"


def _parse_profile(payload: dict[str, Any]) -> TaxProfile:
    buy_tax_bps = _as_bps(payload.get("buy_tax"))
    sell_tax_bps = _as_bps(payload.get("sell_tax"))
    if buy_tax_bps is None or sell_tax_bps is None:
        return TaxProfile(buy_tax_bps, sell_tax_bps, "unknown tax")
    if _is_true(payload, "cannot_buy"):
        return TaxProfile(buy_tax_bps, sell_tax_bps, "cannot buy")
    if _is_true(payload, "cannot_sell_all") or _is_true(payload, "cannot_sell"):
        return TaxProfile(buy_tax_bps, sell_tax_bps, "cannot sell all")
    if _is_true(payload, "is_honeypot"):
        return TaxProfile(buy_tax_bps, sell_tax_bps, "honeypot flag")
    if _is_true(payload, "slippage_modifiable"):
        return TaxProfile(buy_tax_bps, sell_tax_bps, "modifiable tax")
    if _is_true(payload, "trading_cooldown"):
        return TaxProfile(buy_tax_bps, sell_tax_bps, "trading cooldown")
    return TaxProfile(buy_tax_bps, sell_tax_bps)


class GoPlusTaxSource:
    """Fetch one contract's tax profile without logging API credentials."""

    def __init__(self, access_token: str | None = None) -> None:
        self._access_token = (access_token or "").strip()

    async def fetch(self, network: str, address: str) -> TaxProfile:
        chain_id = GOPLUS_NETWORK_IDS.get(network)
        if chain_id is None:
            return TaxProfile(None, None, "unsupported tax-check network")
        key = (chain_id, address.lower())
        cached = _profile_cache.get(key)
        if cached and monotonic() - cached[0] < TAX_CACHE_SECONDS:
            return cached[1]

        headers = (
            {"Authorization": f"Bearer {self._access_token}"}
            if self._access_token
            else {}
        )
        try:
            async with httpx.AsyncClient(timeout=12.0) as client:
                response = await self._get(
                    client,
                    f"{GOPLUS_TOKEN_SECURITY_URL}/{chain_id}",
                    {"contract_addresses": address.lower()},
                    headers,
                )
            if response.status_code != 200:
                return TaxProfile(None, None, "tax API unavailable")
            body = response.json()
            result = body.get("result", {})
            token = result.get(address.lower()) if isinstance(result, dict) else None
            profile = (
                _parse_profile(token)
                if body.get("code") in (1, "1", 0, "0", None) and isinstance(token, dict)
                else TaxProfile(None, None, "tax API returned no token profile")
            )
        except (httpx.HTTPError, ValueError, TypeError):
            profile = TaxProfile(None, None, "tax API unavailable")
        _profile_cache[key] = (monotonic(), profile)
        return profile

    async def _get(
        self,
        client: httpx.AsyncClient,
        url: str,
        params: dict[str, str],
        headers: dict[str, str],
    ) -> httpx.Response:
        global _last_request_at
        async with _rate_lock:
            remaining = GOPLUS_MIN_REQUEST_INTERVAL_SECONDS - (monotonic() - _last_request_at)
            if remaining > 0:
                await asyncio.sleep(remaining)
            response = await client.get(url, params=params, headers=headers)
            _last_request_at = monotonic()
            return response


async def filter_tax_eligible_candidates(
    candidates: list[TokenCandidate],
    max_token_tax_bps: float,
    access_token: str | None = None,
) -> tuple[list[TokenCandidate], list[str]]:
    """Keep only candidates with known, non-modifiable tax below the limit."""

    source = GoPlusTaxSource(access_token)
    accepted: list[TokenCandidate] = []
    rejected: Counter[str] = Counter()
    for candidate in candidates:
        profile = await source.fetch(candidate.network, candidate.address)
        reason = profile.risk_reason
        if reason is None and (
            profile.buy_tax_bps is None
            or profile.sell_tax_bps is None
            or profile.buy_tax_bps > max_token_tax_bps
            or profile.sell_tax_bps > max_token_tax_bps
        ):
            reason = "tax above limit" if profile.buy_tax_bps is not None else "unknown tax"
        if reason is not None:
            rejected[reason] += 1
            continue
        accepted.append(
            replace(
                candidate,
                buy_tax_bps=profile.buy_tax_bps,
                sell_tax_bps=profile.sell_tax_bps,
                tax_status="verified",
            )
        )
    warning = []
    if rejected:
        summary = ", ".join(f"{reason}={count}" for reason, count in sorted(rejected.items()))
        warning.append(
            f"Tax filter rejected {sum(rejected.values())} contract(s): {summary}."
        )
    return accepted, warning
