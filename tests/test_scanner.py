import pytest

import spread_radar.scanner as scanner_module

from spread_radar.errors import UpstreamApiError
from spread_radar.models import CexQuote, TokenCandidate
from spread_radar.scanner import (
    CexQuoteSource,
    GeckoTerminalCandidateSource,
    OkxHotTokenCandidateSource,
    ScanSettings,
    SpreadScanner,
    find_opportunities,
)


def test_settings_rejects_invalid_market_type() -> None:
    settings = ScanSettings(market_types=frozenset({"futures"}))
    try:
        settings.validate()
    except ValueError as error:
        assert "market_types" in str(error)
    else:
        raise AssertionError("invalid market type should have failed")


def test_spread_math_matches_buy_onchain_sell_cex_definition() -> None:
    candidate = TokenCandidate("FOO", "0x1", "bsc", 1.0, 1_000_000, 500_000, "test")
    quote = CexQuote("binance", "FOO/USDT", "spot", 1.03, 1.04)
    gross_bps = (quote.bid / candidate.onchain_price_usd - 1) * 10_000
    assert round(gross_bps) == 300
    assert round(gross_bps - 150) == 150


def test_finder_returns_only_spreads_above_net_threshold() -> None:
    candidate = TokenCandidate("FOO", "0x1", "bsc", 1.0, 1_000_000, 500_000, "test")
    quotes = [
        CexQuote("binance", "FOO/USDT", "spot", 1.03, 1.04),
        CexQuote("okx", "FOO/USDT:USDT", "swap", 1.01, 1.02),
    ]

    opportunities = find_opportunities([candidate], quotes, all_in_cost_bps=150, min_net_bps=100)

    assert len(opportunities) == 1
    assert opportunities[0].quote.exchange == "binance"
    assert round(opportunities[0].net_spread_bps) == 150


def test_finder_deducts_verified_buy_and_sell_tax_from_net_spread() -> None:
    candidate = TokenCandidate(
        "FOO", "0x1", "bsc", 1.0, 1_000_000, 500_000, "test", 500, 500, "verified"
    )
    quote = CexQuote("binance", "FOO/USDT", "spot", 1.20, 1.21)

    opportunities = find_opportunities([candidate], [quote], all_in_cost_bps=0, min_net_bps=0)

    assert len(opportunities) == 1
    assert round(opportunities[0].token_tax_cost_bps) == 975
    assert round(opportunities[0].net_spread_bps) == 1025


def test_finder_excludes_gross_spreads_above_1000_percent_as_ticker_collisions() -> None:
    candidate = TokenCandidate("FOO", "0x1", "bsc", 1.0, 1_000_000, 500_000, "test")
    quotes = [
        CexQuote("binance", "FOO/USDT", "spot", 11.0, 11.1),  # exactly 1,000%
        CexQuote("gate", "FOO/USDT", "spot", 11.01, 11.1),  # above 1,000%
    ]

    opportunities = find_opportunities([candidate], quotes, all_in_cost_bps=0, min_net_bps=0)

    assert [item.quote.exchange for item in opportunities] == ["binance"]


def test_settings_rejects_an_unsafe_cex_request_interval() -> None:
    settings = ScanSettings(cex_min_interval_ms=99)
    try:
        settings.validate()
    except ValueError as error:
        assert "cex_min_interval_ms" in str(error)
    else:
        raise AssertionError("an unsafe CEX interval should have failed")


def test_settings_rejects_an_invalid_cex_timeout() -> None:
    settings = ScanSettings(cex_timeout_ms=999)
    try:
        settings.validate()
    except ValueError as error:
        assert "cex_timeout_ms" in str(error)
    else:
        raise AssertionError("an unsafe CEX timeout should have failed")


@pytest.mark.anyio
async def test_okx_without_credentials_falls_back_to_gecko(monkeypatch) -> None:
    candidate = TokenCandidate("FOO", "0x1", "bsc", 1.0, 1_000, 500, "geckoterminal")
    fallback_networks = []

    async def unavailable_okx(self, network, limit):
        raise UpstreamApiError("HTTP 402")

    async def fallback_gecko(self, network, limit):
        fallback_networks.append(network)
        return [candidate]

    async def no_quotes(self, candidates, market_types):
        return [], []

    async def tax_eligible(candidates, max_tax_bps, access_token):
        return candidates, []

    monkeypatch.setattr(OkxHotTokenCandidateSource, "fetch", unavailable_okx)
    monkeypatch.setattr(GeckoTerminalCandidateSource, "fetch", fallback_gecko)
    monkeypatch.setattr(CexQuoteSource, "fetch_all", no_quotes)
    monkeypatch.setattr(scanner_module, "filter_tax_eligible_candidates", tax_eligible)

    result = await SpreadScanner(
        ScanSettings(source="okx", network="56", exchanges=("gate",))
    ).scan_once()

    assert result.candidates == (candidate,)
    assert fallback_networks == ["bsc"]
    assert result.warnings == (
        "OKX Hot Token needs local API credentials; used the free "
        "GeckoTerminal fallback on bsc for this scan.",
    )
