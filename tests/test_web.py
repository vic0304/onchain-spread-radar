import httpx
import pytest

from spread_radar.models import CexQuote, SpreadOpportunity, TokenCandidate
from spread_radar.scanner import ScanResult
from spread_radar import web


@pytest.mark.anyio
async def test_dashboard_page_is_available() -> None:
    transport = httpx.ASGITransport(app=web.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/")

    assert response.status_code == 200
    assert "链上 / CEX 价差雷达" in response.text


@pytest.mark.anyio
async def test_scan_api_exposes_a_read_only_result(monkeypatch) -> None:
    token = TokenCandidate("FOO", "0x1", "bsc", 1.0, 1000, 500, "test")
    quote = CexQuote("binance", "FOO/USDT", "spot", 1.03, 1.04)
    result = ScanResult(
        candidates=(token,),
        quotes=(quote,),
        opportunities=(SpreadOpportunity(token, quote, 300, 150, 0, 150),),
        warnings=(),
    )

    async def fake_scan_once(self):
        return result

    monkeypatch.setattr(web.SpreadScanner, "scan_once", fake_scan_once)
    web._cache = None
    transport = httpx.ASGITransport(app=web.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/scan?candidate_limit=1")

    assert response.status_code == 200
    assert response.json()["opportunities"][0]["token"]["symbol"] == "FOO"


@pytest.mark.anyio
async def test_identical_scan_returns_a_cached_result(monkeypatch) -> None:
    token = TokenCandidate("FOO", "0x1", "bsc", 1.0, 1000, 500, "test")
    quote = CexQuote("binance", "FOO/USDT", "spot", 1.03, 1.04)
    result = ScanResult((token,), (quote,), (), ())
    call_count = 0

    async def fake_scan_once(self):
        nonlocal call_count
        call_count += 1
        return result

    monkeypatch.setattr(web.SpreadScanner, "scan_once", fake_scan_once)
    web._cache = None
    transport = httpx.ASGITransport(app=web.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.get("/api/scan?candidate_limit=1")
        second = await client.get("/api/scan?candidate_limit=1")

    assert first.status_code == second.status_code == 200
    assert first.json()["cached"] is False
    assert second.json()["cached"] is True
    assert call_count == 1
