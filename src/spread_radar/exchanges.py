from __future__ import annotations

import asyncio
from collections.abc import Iterable

import ccxt.async_support as ccxt

from .models import CexQuote, TokenCandidate

DEFAULT_EXCHANGES = ("binance", "okx", "bybit", "gate")
VALID_MARKET_TYPES = frozenset({"spot", "swap"})


def _matches_candidate(market: dict, candidate: TokenCandidate, market_types: set[str]) -> bool:
    if not market.get("active", True):
        return False
    if str(market.get("base", "")).upper() != candidate.symbol:
        return False
    if str(market.get("quote", "")).upper() not in {"USDT", "USDC"}:
        return False
    if market.get("spot"):
        return "spot" in market_types
    if market.get("swap") and market.get("linear"):
        return "swap" in market_types
    return False


def _market_type(market: dict) -> str:
    return "spot" if market.get("spot") else "swap"


def _depth_usd(levels: list[object], maximum_levels: int = 5) -> tuple[float, int]:
    """Return executable notional visible in the requested top book levels."""

    depth = 0.0
    counted_levels = 0
    for level in levels[:maximum_levels]:
        try:
            price, amount = float(level[0]), float(level[1])
        except (IndexError, TypeError, ValueError):
            continue
        if price <= 0 or amount <= 0:
            continue
        depth += price * amount
        counted_levels += 1
    return depth, counted_levels


class CexQuoteSource:
    """Fetch top-of-book CEX quotes without credentials or trading rights."""

    def __init__(
        self,
        exchange_ids: Iterable[str],
        concurrency: int = 8,
        min_interval_ms: int = 250,
        timeout_ms: int = 5_000,
    ) -> None:
        self._exchange_ids = tuple(exchange_ids)
        self._semaphore = asyncio.Semaphore(concurrency)
        self._min_interval_ms = min_interval_ms
        self._timeout_ms = timeout_ms

    async def fetch_all(
        self, candidates: list[TokenCandidate], market_types: set[str]
    ) -> tuple[list[CexQuote], list[str]]:
        exchanges = []
        errors: list[str] = []
        for exchange_id in self._exchange_ids:
            exchange_class = getattr(ccxt, exchange_id, None)
            if exchange_class is None:
                errors.append(f"Unsupported CCXT exchange id: {exchange_id}")
                continue
            exchanges.append(
                (
                    exchange_id,
                    exchange_class(
                        {
                            "enableRateLimit": True,
                            # Exchange endpoints have different weights. This
                            # conservative floor caps this radar at roughly 4
                            # requests/second per venue, even if CCXT ships a
                            # more aggressive default for a public endpoint.
                            "rateLimit": self._min_interval_ms,
                            # Public endpoints that are unreachable in a
                            # region should not freeze the whole dashboard.
                            "timeout": self._timeout_ms,
                        }
                    ),
                )
            )

        try:
            loaded = await asyncio.gather(
                *(self._load_markets(exchange_id, exchange) for exchange_id, exchange in exchanges)
            )
            for _, load_errors in loaded:
                errors.extend(load_errors)

            tasks = []
            for (exchange_id, exchange), (markets, load_errors) in zip(exchanges, loaded):
                if load_errors:
                    continue
                for candidate in candidates:
                    matches = [
                        market for market in markets.values()
                        if _matches_candidate(market, candidate, market_types)
                    ]
                    for market in matches:
                        tasks.append(self._fetch_book(exchange_id, exchange, market))
            outcomes = await asyncio.gather(*tasks, return_exceptions=True)
            quotes: list[CexQuote] = []
            for outcome in outcomes:
                if isinstance(outcome, Exception):
                    errors.append(str(outcome))
                elif outcome is not None:
                    quotes.append(outcome)
            return quotes, errors
        finally:
            await asyncio.gather(*(exchange.close() for _, exchange in exchanges), return_exceptions=True)

    async def _load_markets(self, exchange_id: str, exchange: ccxt.Exchange) -> tuple[dict, list[str]]:
        try:
            return await exchange.load_markets(), []
        except Exception as error:  # CCXT error types differ by exchange.
            return {}, [f"{exchange_id}: could not load markets ({error!r})"]

    async def _fetch_book(
        self, exchange_id: str, exchange: ccxt.Exchange, market: dict
    ) -> CexQuote | None:
        async with self._semaphore:
            try:
                book = await exchange.fetch_order_book(market["symbol"], limit=5)
            except Exception as error:  # A delisted or illiquid market is normal here.
                raise RuntimeError(f"{exchange_id} {market['symbol']}: {error}") from error
        bids, asks = book.get("bids") or [], book.get("asks") or []
        if not bids or not asks:
            return None
        bid = float(bids[0][0])
        ask = float(asks[0][0])
        if bid <= 0 or ask <= 0:
            return None
        bid_depth_usd, bid_levels = _depth_usd(bids)
        ask_depth_usd, ask_levels = _depth_usd(asks)
        return CexQuote(
            exchange=exchange_id,
            market_symbol=market["symbol"],
            market_type=_market_type(market),
            bid=bid,
            ask=ask,
            bid_depth_usd=bid_depth_usd,
            ask_depth_usd=ask_depth_usd,
            bid_levels=bid_levels,
            ask_levels=ask_levels,
        )
