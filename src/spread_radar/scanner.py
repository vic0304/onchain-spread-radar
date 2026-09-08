from __future__ import annotations

from dataclasses import dataclass

from .candidates import GeckoTerminalCandidateSource, OkxHotTokenCandidateSource
from .errors import UpstreamApiError
from .exchanges import CexQuoteSource, DEFAULT_EXCHANGES, VALID_MARKET_TYPES
from .models import CexQuote, SpreadOpportunity, TokenCandidate
from .security import filter_tax_eligible_candidates

OKX_CHAIN_TO_GECKO_NETWORK = {
    "1": "eth",
    "10": "optimism",
    "56": "bsc",
    "137": "polygon_pos",
    "8453": "base",
    "42161": "arbitrum",
}
# A CEX ticker matching a token symbol is not sufficient proof that both legs
# are the same asset.  Larger apparent dislocations are overwhelmingly ticker
# collisions, stale/illiquid books, or bad source data rather than a usable
# opportunity.  This is intentionally a hard safety ceiling: 1,000% = 100,000
# basis points.  Exactly 1,000% remains visible; only larger values are cut.
MAX_PLAUSIBLE_GROSS_SPREAD_BPS = 100_000.0


@dataclass(frozen=True, slots=True)
class ScanSettings:
    source: str = "geckoterminal"
    network: str = "bsc"
    candidate_limit: int = 100
    exchanges: tuple[str, ...] = DEFAULT_EXCHANGES
    market_types: frozenset[str] = frozenset({"spot", "swap"})
    all_in_cost_bps: float = 150.0
    min_net_bps: float = 50.0
    cex_min_interval_ms: int = 250
    cex_timeout_ms: int = 5_000
    max_token_tax_bps: float = 500.0
    goplus_access_token: str | None = None
    x402_payment: str | None = None
    okx_access_key: str | None = None
    okx_secret_key: str | None = None
    okx_passphrase: str | None = None

    def validate(self) -> None:
        if self.source not in {"geckoterminal", "okx"}:
            raise ValueError("source must be geckoterminal or okx")
        if not 1 <= self.candidate_limit <= 100:
            raise ValueError("candidate_limit must be between 1 and 100")
        if not self.market_types or not self.market_types <= VALID_MARKET_TYPES:
            raise ValueError("market_types may contain only spot and swap")
        if self.all_in_cost_bps < 0 or self.min_net_bps < 0:
            raise ValueError("cost and threshold must be non-negative")
        if not 0 <= self.max_token_tax_bps <= 10_000:
            raise ValueError("max_token_tax_bps must be between 0 and 10000")
        if self.cex_min_interval_ms < 100:
            raise ValueError("cex_min_interval_ms must be at least 100")
        if not 1_000 <= self.cex_timeout_ms <= 30_000:
            raise ValueError("cex_timeout_ms must be between 1000 and 30000")


@dataclass(frozen=True, slots=True)
class ScanResult:
    candidates: tuple[TokenCandidate, ...]
    quotes: tuple[CexQuote, ...]
    opportunities: tuple[SpreadOpportunity, ...]
    warnings: tuple[str, ...]


def find_opportunities(
    candidates: list[TokenCandidate],
    quotes: list[CexQuote],
    all_in_cost_bps: float,
    min_net_bps: float,
) -> tuple[SpreadOpportunity, ...]:
    """Rank only the executable-side screen: DEX reference buy, CEX best bid sell."""

    candidate_by_symbol = {candidate.symbol: candidate for candidate in candidates}
    opportunities: list[SpreadOpportunity] = []
    for quote in quotes:
        base = quote.market_symbol.split("/")[0].upper()
        candidate = candidate_by_symbol.get(base)
        if candidate is None or candidate.onchain_price_usd <= 0:
            continue
        gross_spread_bps = (quote.bid / candidate.onchain_price_usd - 1.0) * 10_000
        if gross_spread_bps > MAX_PLAUSIBLE_GROSS_SPREAD_BPS:
            continue
        # Tax is a first-class, contract-specific cost. It is not included in
        # the user supplied general buffer because fee-on-transfer tokens can
        # have asymmetric, extremely high taxes. The exact compound drag is
        # conservative for the chain buy / eventual chain sell lifecycle.
        buy_tax = (candidate.buy_tax_bps or 0.0) / 10_000
        sell_tax = (candidate.sell_tax_bps or 0.0) / 10_000
        token_tax_cost_bps = (1 - (1 - buy_tax) * (1 - sell_tax)) * 10_000
        net_spread_bps = gross_spread_bps - all_in_cost_bps - token_tax_cost_bps
        if net_spread_bps >= min_net_bps:
            opportunities.append(
                SpreadOpportunity(
                    token=candidate,
                    quote=quote,
                    gross_spread_bps=gross_spread_bps,
                    all_in_cost_bps=all_in_cost_bps,
                    token_tax_cost_bps=token_tax_cost_bps,
                    net_spread_bps=net_spread_bps,
                )
            )
    return tuple(sorted(opportunities, key=lambda item: item.net_spread_bps, reverse=True))


class SpreadScanner:
    """Read-only scanner for potential on-chain buy / CEX sell dislocations."""

    def __init__(self, settings: ScanSettings) -> None:
        settings.validate()
        self.settings = settings

    async def scan_once(self) -> ScanResult:
        warnings: list[str] = []
        if self.settings.source == "geckoterminal":
            candidates = await GeckoTerminalCandidateSource().fetch(
                self.settings.network, self.settings.candidate_limit
            )
        else:
            source = OkxHotTokenCandidateSource(
                x402_payment=self.settings.x402_payment,
                access_key=self.settings.okx_access_key,
                secret_key=self.settings.okx_secret_key,
                passphrase=self.settings.okx_passphrase,
            )
            try:
                candidates = await source.fetch(
                    self.settings.network, self.settings.candidate_limit
                )
            except UpstreamApiError:
                # An unauthenticated OKX request only adds friction for a
                # read-only radar.  Fall back only when no partial credential
                # or payment proof was supplied; those cases should remain
                # visible configuration errors rather than being hidden.
                no_okx_auth = not any(
                    (
                        self.settings.okx_access_key,
                        self.settings.okx_secret_key,
                        self.settings.okx_passphrase,
                        self.settings.x402_payment,
                    )
                )
                if not no_okx_auth:
                    raise
                gecko_network = OKX_CHAIN_TO_GECKO_NETWORK.get(
                    self.settings.network, self.settings.network
                )
                candidates = await GeckoTerminalCandidateSource().fetch(
                    gecko_network, self.settings.candidate_limit
                )
                warnings.append(
                    "OKX Hot Token needs local API credentials; used the free "
                    f"GeckoTerminal fallback on {gecko_network} for this scan."
                )
        if not candidates:
            raise UpstreamApiError("No usable on-chain candidates were returned.")

        candidates, tax_warnings = await filter_tax_eligible_candidates(
            candidates,
            self.settings.max_token_tax_bps,
            self.settings.goplus_access_token,
        )
        warnings.extend(tax_warnings)
        if not candidates:
            return ScanResult((), (), (), tuple(warnings))

        quotes, cex_warnings = await CexQuoteSource(
            self.settings.exchanges,
            min_interval_ms=self.settings.cex_min_interval_ms,
            timeout_ms=self.settings.cex_timeout_ms,
        ).fetch_all(
            candidates, set(self.settings.market_types)
        )
        return ScanResult(
            candidates=tuple(candidates),
            quotes=tuple(quotes),
            opportunities=find_opportunities(
                candidates,
                quotes,
                self.settings.all_in_cost_bps,
                self.settings.min_net_bps,
            ),
            warnings=tuple([*warnings, *cex_warnings]),
        )
