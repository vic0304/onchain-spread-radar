"""Minimal Telegram Bot API client used only for outbound alert messages."""

from __future__ import annotations

import asyncio
import json as json_module
from collections.abc import Sequence
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener

import httpx

from .errors import NotificationError
from .models import SpreadOpportunity


def format_alert(opportunities: Sequence[SpreadOpportunity]) -> str:
    """Produce one concise, plain-text message within Telegram's limits."""

    lines = [
        "链上 / CEX 价差提醒（仅筛选，非交易指令）",
        "链上参考买入价低于 CEX 最优买价，已扣除你设定的成本缓冲：",
        "",
    ]
    for item in opportunities:
        token = item.token
        quote = item.quote
        lines.extend(
            (
                f"{token.symbol} · {token.network} · {quote.exchange} {quote.market_type}",
                f"链上 ${token.onchain_price_usd:.8g} → CEX 买价 ${quote.bid:.8g}",
                f"毛价差 {item.gross_spread_bps / 100:.2f}% | 预设成本 "
                f"{item.all_in_cost_bps / 100:.2f}% | 代币税 "
                f"{item.token_tax_cost_bps / 100:.2f}% | 净价差 {item.net_spread_bps / 100:.2f}%",
                f"买税 {(token.buy_tax_bps or 0) / 100:.2f}% | 卖税 {(token.sell_tax_bps or 0) / 100:.2f}%",
                f"24h 链上量 ${token.volume_24h_usd:,.0f} | 流动性 ${token.liquidity_usd:,.0f}",
                f"合约：{token.address}",
                "",
            )
        )
    lines.append("请自行核验同名资产、盘口深度、充提状态、滑点、Gas 与资金费。")
    return "\n".join(lines)


class TelegramNotifier:
    """Send alerts to one pre-authorized Telegram chat.

    The bot token is intentionally read only from the local environment.  It
    is never returned to the dashboard, written to logs, or accepted over an
    HTTP endpoint.
    """

    def __init__(
        self,
        bot_token: str | None,
        chat_id: str | None,
        proxy_url: str | None = None,
    ) -> None:
        self._bot_token = (bot_token or "").strip()
        self._chat_id = (chat_id or "").strip()
        self._proxy_url = (proxy_url or "").strip() or None
        if not self._bot_token or not self._chat_id:
            raise ValueError(
                "Telegram alerts need TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env"
            )

    async def verify_recipient(self) -> None:
        """Verify the bot token and target chat without sending a message."""

        endpoint = f"https://api.telegram.org/bot{self._bot_token}/getChat"
        await self._request(endpoint, params={"chat_id": self._chat_id})

    async def identity(self) -> str:
        """Return the configured bot's public username without exposing its token."""

        endpoint = f"https://api.telegram.org/bot{self._bot_token}/getMe"
        payload = await self._request(endpoint)
        result = payload.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("username"), str):
            raise NotificationError("Telegram returned no bot username.")
        return result["username"]

    async def verify_identity(self, expected_username: str | None) -> str:
        """Refuse delivery when the token belongs to a different public Bot."""

        expected = (expected_username or "").strip().lstrip("@").casefold()
        if not expected:
            raise ValueError(
                "Set TELEGRAM_EXPECTED_BOT_USERNAME to prevent using the wrong Bot."
            )
        actual = await self.identity()
        if actual.casefold() != expected:
            raise ValueError(
                f"Telegram Bot mismatch: expected @{expected}, got @{actual}. No message sent."
            )
        return actual

    async def send(self, opportunities: Sequence[SpreadOpportunity]) -> None:
        if not opportunities:
            return
        # Bot tokens are part of Telegram's API path.  Do not surface the URL
        # from caught HTTP errors, because that could expose the token.
        endpoint = f"https://api.telegram.org/bot{self._bot_token}/sendMessage"
        payload = {"chat_id": self._chat_id, "text": format_alert(opportunities)}
        await self._request(endpoint, json=payload)

    async def send_text(self, text: str) -> None:
        """Send an explicit user-requested operational test message."""

        endpoint = f"https://api.telegram.org/bot{self._bot_token}/sendMessage"
        await self._request(endpoint, json={"chat_id": self._chat_id, "text": text})

    async def _request(
        self,
        endpoint: str,
        *,
        params: dict[str, str] | None = None,
        json: dict[str, str] | None = None,
    ) -> dict[str, object]:
        try:
            async with httpx.AsyncClient(timeout=10.0, proxy=self._proxy_url) as client:
                response = await client.request("POST" if json is not None else "GET", endpoint, params=params, json=json)
        except httpx.HTTPError as error:
            # Some Windows proxy/TLS stacks work with urllib but not httpx.
            # Keep this transport fallback inside this project; it never
            # imports another project's configuration or credentials.
            try:
                response_status, body = await asyncio.to_thread(
                    self._urllib_request, endpoint, params, json
                )
            except OSError as fallback_error:
                raise NotificationError("Telegram request could not be completed.") from fallback_error
        else:
            response_status, body = response.status_code, response.content
        if response_status >= 400:
            raise NotificationError(
                f"Telegram rejected the alert (HTTP {response_status})."
            )
        try:
            parsed = json_module.loads(body)
        except (TypeError, UnicodeDecodeError, json_module.JSONDecodeError) as error:
            raise NotificationError("Telegram returned an invalid response.") from error
        if parsed.get("ok") is not True:
            raise NotificationError("Telegram did not accept the alert.")
        return parsed

    def _urllib_request(
        self,
        endpoint: str,
        params: dict[str, str] | None,
        payload: dict[str, str] | None,
    ) -> tuple[int, bytes]:
        if params:
            endpoint = f"{endpoint}?{urlencode(params)}"
        data = urlencode(payload).encode("utf-8") if payload is not None else None
        request = Request(
            endpoint,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"} if data else {},
            method="POST" if data is not None else "GET",
        )
        handlers = (
            [ProxyHandler({"http": self._proxy_url, "https": self._proxy_url})]
            if self._proxy_url
            else []
        )
        with build_opener(*handlers).open(request, timeout=20) as response:
            return response.status, response.read()
