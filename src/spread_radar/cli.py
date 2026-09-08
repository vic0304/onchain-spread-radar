from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from .config import load_local_env
from .errors import UpstreamApiError
from .exchanges import DEFAULT_EXCHANGES
from .scanner import ScanResult, ScanSettings, SpreadScanner


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only on-chain/CEX spread radar. It never trades or stores keys."
    )
    parser.add_argument("--source", choices=("geckoterminal", "okx"), default="geckoterminal")
    parser.add_argument(
        "--network",
        default="bsc",
        help="GeckoTerminal network id (e.g. bsc, eth, base); OKX chain index when --source okx.",
    )
    parser.add_argument("--candidate-limit", type=int, default=100)
    parser.add_argument(
        "--exchanges",
        default=",".join(DEFAULT_EXCHANGES),
        help="Comma-separated CCXT ids. Default: binance,okx,bybit,gate",
    )
    parser.add_argument(
        "--market-types",
        default="spot,swap",
        help="Comma-separated: spot,swap. Default: both.",
    )
    parser.add_argument(
        "--all-in-cost-bps",
        type=float,
        default=150.0,
        help="Conservative total cost buffer in bps: both legs, gas, slippage, funding and failures.",
    )
    parser.add_argument(
        "--min-net-bps",
        type=float,
        default=50.0,
        help="Only show opportunities whose gross spread minus all-in cost reaches this many bps.",
    )
    parser.add_argument(
        "--max-token-tax-bps",
        type=float,
        default=float(os.getenv("GOPLUS_MAX_TOKEN_TAX_BPS", "500")),
        help="Reject a contract when either buy or sell tax exceeds this many bps. Default: 500.",
    )
    parser.add_argument(
        "--cex-min-interval-ms",
        type=int,
        default=250,
        help="Safety floor between requests to the same CEX. Default: 250 ms (about 4 req/s).",
    )
    parser.add_argument(
        "--cex-timeout-ms",
        type=int,
        default=5_000,
        help="Per-request CEX timeout. Default: 5000 ms; failed venues become warnings.",
    )
    parser.add_argument(
        "--interval-seconds",
        type=float,
        default=0.0,
        help="Repeat scan at this interval. 0 means run once.",
    )
    parser.add_argument("--json-output", type=Path, help="Optional path for a JSON scan record.")
    return parser.parse_args()


def _serialize(result: ScanResult) -> dict:
    return {
        "scanned_at": datetime.now(UTC).isoformat(),
        "candidate_count": len(result.candidates),
        "quote_count": len(result.quotes),
        "candidates": [asdict(item) for item in result.candidates],
        "opportunities": [item.to_dict() for item in result.opportunities],
        "warnings": list(result.warnings),
    }


def _print_result(result: ScanResult, settings: ScanSettings) -> None:
    print(
        f"{len(result.candidates)} on-chain candidates | {len(result.quotes)} CEX books | "
        f"{len(result.opportunities)} possible spreads\n"
    )
    if not result.opportunities:
        print("No opportunities passed the configured net-spread threshold.")
    else:
        print("Potential on-chain buy / CEX sell setups (screening only):")
        print(
            f"{'token':<12} {'venue':<9} {'market':<5} {'dex px':>12} {'cex bid':>12} "
            f"{'gross':>9} {'net':>9} {'24h vol':>13} {'liquidity':>13}"
        )
        for item in result.opportunities:
            print(
                f"{item.token.symbol:<12} {item.quote.exchange:<9} {item.quote.market_type:<5} "
                f"{item.token.onchain_price_usd:>12.8g} {item.quote.bid:>12.8g} "
                f"{item.gross_spread_bps / 100:>8.2f}% {item.net_spread_bps / 100:>8.2f}% "
                f"{item.token.volume_24h_usd:>13.0f} {item.token.liquidity_usd:>13.0f}"
            )
    if result.warnings:
        print(f"\n{len(result.warnings)} non-fatal data warning(s); use --json-output to inspect them.")
    print(
        "\nWarning: a positive screen is not executable P&L. Verify contract identity, order-book depth, "
        "withdrawals, bridge capacity, funding, liquidation risk and both legs before trading."
    )


async def _run(args: argparse.Namespace) -> int:
    settings = ScanSettings(
        source=args.source,
        network=args.network,
        candidate_limit=args.candidate_limit,
        exchanges=tuple(item.strip() for item in args.exchanges.split(",") if item.strip()),
        market_types=frozenset(item.strip() for item in args.market_types.split(",") if item.strip()),
        all_in_cost_bps=args.all_in_cost_bps,
        min_net_bps=args.min_net_bps,
        max_token_tax_bps=args.max_token_tax_bps,
        cex_min_interval_ms=args.cex_min_interval_ms,
        cex_timeout_ms=args.cex_timeout_ms,
        x402_payment=os.getenv("OKX_X402_PAYMENT"),
        okx_access_key=os.getenv("OKX_ACCESS_KEY"),
        okx_secret_key=os.getenv("OKX_SECRET_KEY"),
        okx_passphrase=os.getenv("OKX_PASSPHRASE"),
        goplus_access_token=os.getenv("GOPLUS_ACCESS_TOKEN"),
    )
    scanner = SpreadScanner(settings)
    while True:
        result = await scanner.scan_once()
        _print_result(result, settings)
        if args.json_output:
            args.json_output.parent.mkdir(parents=True, exist_ok=True)
            args.json_output.write_text(
                json.dumps(_serialize(result), ensure_ascii=False, indent=2), encoding="utf-8"
            )
        if args.interval_seconds <= 0:
            return 0
        await asyncio.sleep(args.interval_seconds)


def main() -> None:
    load_local_env()
    args = _parse_args()
    try:
        raise SystemExit(asyncio.run(_run(args)))
    except (ValueError, UpstreamApiError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2) from error
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
