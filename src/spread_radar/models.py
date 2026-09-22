from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class TokenCandidate:
    """A token selected from a high-volume on-chain pool/list."""

    symbol: str
    address: str
    network: str
    onchain_price_usd: float
    volume_24h_usd: float
    liquidity_usd: float
    source: str
    buy_tax_bps: float | None = None
    sell_tax_bps: float | None = None
    tax_status: str = "unchecked"


@dataclass(frozen=True, slots=True)
class CexQuote:
    exchange: str
    market_symbol: str
    market_type: str
    bid: float
    ask: float
    bid_depth_usd: float = 0.0
    ask_depth_usd: float = 0.0
    bid_levels: int = 0
    ask_levels: int = 0


@dataclass(frozen=True, slots=True)
class SpreadOpportunity:
    """A screening result, not an instruction to trade."""

    token: TokenCandidate
    quote: CexQuote
    gross_spread_bps: float
    all_in_cost_bps: float
    token_tax_cost_bps: float
    net_spread_bps: float

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["token"] = asdict(self.token)
        payload["quote"] = asdict(self.quote)
        return payload
