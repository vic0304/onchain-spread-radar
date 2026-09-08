from __future__ import annotations

import asyncio
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse

from .config import load_local_env
from .errors import UpstreamApiError
from .scanner import ScanResult, ScanSettings, SpreadScanner

STATIC_DIR = Path(__file__).parent / "static"
CACHE_TTL = timedelta(seconds=60)

app = FastAPI(title="链上 / CEX 价差雷达", docs_url=None, redoc_url=None)
_scan_lock = asyncio.Lock()


@dataclass
class _CacheEntry:
    key: tuple[object, ...]
    created_at: datetime
    payload: dict


_cache: _CacheEntry | None = None


def _payload(result: ScanResult, cached: bool) -> dict:
    return {
        "scanned_at": datetime.now(UTC).isoformat(),
        "cached": cached,
        "candidate_count": len(result.candidates),
        "quote_count": len(result.quotes),
        "candidates": [asdict(item) for item in result.candidates],
        "opportunities": [item.to_dict() for item in result.opportunities],
        "warnings": list(result.warnings),
    }


@app.get("/", response_class=FileResponse)
async def dashboard() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/static/{asset_name}", response_class=FileResponse)
async def static_asset(asset_name: str) -> FileResponse:
    if asset_name not in {"app.js", "styles.css"}:
        raise HTTPException(status_code=404, detail="Asset not found")
    return FileResponse(STATIC_DIR / asset_name)


@app.get("/api/scan")
async def scan(
    source: str = Query("geckoterminal", pattern="^(geckoterminal|okx)$"),
    network: str = Query("bsc", min_length=2, max_length=32),
    candidate_limit: int = Query(20, ge=1, le=100),
    exchanges: str = Query("gate"),
    market_types: str = Query("spot,swap"),
    all_in_cost_bps: float = Query(150, ge=0, le=10_000),
    min_net_bps: float = Query(50, ge=0, le=10_000),
    max_token_tax_bps: float = Query(500, ge=0, le=10_000),
) -> dict:
    global _cache
    parsed_types = frozenset(item.strip() for item in market_types.split(",") if item.strip())
    settings = ScanSettings(
        source=source,
        network=network,
        candidate_limit=candidate_limit,
        exchanges=tuple(item.strip() for item in exchanges.split(",") if item.strip()),
        market_types=parsed_types,
        all_in_cost_bps=all_in_cost_bps,
        min_net_bps=min_net_bps,
        max_token_tax_bps=max_token_tax_bps,
        x402_payment=os.getenv("OKX_X402_PAYMENT"),
        okx_access_key=os.getenv("OKX_ACCESS_KEY"),
        okx_secret_key=os.getenv("OKX_SECRET_KEY"),
        okx_passphrase=os.getenv("OKX_PASSPHRASE"),
        goplus_access_token=os.getenv("GOPLUS_ACCESS_TOKEN"),
    )
    try:
        settings.validate()
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    key = (
        settings.source,
        settings.network,
        settings.candidate_limit,
        settings.exchanges,
        tuple(sorted(settings.market_types)),
        settings.all_in_cost_bps,
        settings.min_net_bps,
        settings.max_token_tax_bps,
    )
    now = datetime.now(UTC)
    async with _scan_lock:
        if _cache and _cache.key == key and now - _cache.created_at < CACHE_TTL:
            return {**_cache.payload, "cached": True}
        try:
            result = await SpreadScanner(settings).scan_once()
        except UpstreamApiError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        payload = _payload(result, cached=False)
        # The scan itself can take longer than a minute on public APIs. Cache
        # from completion time, otherwise a fresh result can expire instantly.
        _cache = _CacheEntry(key=key, created_at=datetime.now(UTC), payload=payload)
        return payload


def main() -> None:
    """Run the local, read-only dashboard; it never binds to the public network."""

    load_local_env()
    port = int(os.getenv("SPREAD_RADAR_PORT", "8000"))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
