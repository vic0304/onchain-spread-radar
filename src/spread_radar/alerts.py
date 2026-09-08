"""Long-running, de-duplicated Telegram alerts for the read-only radar."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import UTC, datetime, timedelta

import httpx

from .config import load_local_env
from .errors import NotificationError, UpstreamApiError
from .exchanges import DEFAULT_EXCHANGES
from .models import SpreadOpportunity
from .scanner import ScanSettings, SpreadScanner
from .telegram import TelegramNotifier


class AlertDeduplicator:
    """Suppress repeats for the same contract/CEX market during a cooldown."""

    def __init__(self, cooldown_seconds: float) -> None:
        if cooldown_seconds < 0:
            raise ValueError("cooldown_seconds must be non-negative")
        self._cooldown = timedelta(seconds=cooldown_seconds)
        self._last_sent: dict[tuple[str, str, str, str], datetime] = {}

    @staticmethod
    def _key(item: SpreadOpportunity) -> tuple[str, str, str, str]:
        return (
            item.token.address.lower(),
            item.token.network,
            item.quote.exchange,
            item.quote.market_symbol,
        )

    def eligible(
        self, opportunities: tuple[SpreadOpportunity, ...], now: datetime
    ) -> list[SpreadOpportunity]:
        return [
            item
            for item in opportunities
            if (previous := self._last_sent.get(self._key(item))) is None
            or now - previous >= self._cooldown
        ]

    def mark_sent(self, opportunities: list[SpreadOpportunity], now: datetime) -> None:
        for item in opportunities:
            self._last_sent[self._key(item)] = now


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only on-chain/CEX scanner with Telegram alerts. It never trades."
    )
    parser.add_argument("--source", choices=("geckoterminal", "okx"), default="geckoterminal")
    parser.add_argument("--network", default="bsc")
    parser.add_argument("--candidate-limit", type=int, default=20)
    parser.add_argument("--exchanges", default="gate")
    parser.add_argument("--market-types", default="spot,swap")
    parser.add_argument("--all-in-cost-bps", type=float, default=150.0)
    parser.add_argument("--min-net-bps", type=float, default=50.0)
    parser.add_argument(
        "--max-token-tax-bps",
        type=float,
        default=float(os.getenv("GOPLUS_MAX_TOKEN_TAX_BPS", "500")),
    )
    parser.add_argument("--cex-min-interval-ms", type=int, default=250)
    parser.add_argument("--cex-timeout-ms", type=int, default=5_000)
    parser.add_argument(
        "--interval-seconds",
        type=float,
        default=float(os.getenv("TELEGRAM_POLL_SECONDS", "90")),
        help="Seconds between completed scans. Default: TELEGRAM_POLL_SECONDS or 90.",
    )
    parser.add_argument(
        "--cooldown-seconds",
        type=float,
        default=float(os.getenv("TELEGRAM_ALERT_COOLDOWN_SECONDS", "900")),
        help="Minimum time before the same contract/CEX market can alert again. Default: 900.",
    )
    parser.add_argument(
        "--max-alerts-per-scan",
        type=int,
        default=int(os.getenv("TELEGRAM_ALERT_MAX_PER_SCAN", "3")),
        help="Maximum highest-ranked opportunities in one message. Default: 3.",
    )
    return parser.parse_args()


def _settings_from(args: argparse.Namespace) -> ScanSettings:
    return ScanSettings(
        source=args.source,
        network=args.network,
        candidate_limit=args.candidate_limit,
        exchanges=tuple(item.strip() for item in args.exchanges.split(",") if item.strip()),
        market_types=frozenset(
            item.strip() for item in args.market_types.split(",") if item.strip()
        ),
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


async def _run(args: argparse.Namespace) -> int:
    if args.interval_seconds < 30:
        raise ValueError("interval_seconds must be at least 30 to respect public APIs")
    if args.max_alerts_per_scan < 1 or args.max_alerts_per_scan > 10:
        raise ValueError("max_alerts_per_scan must be between 1 and 10")

    settings = _settings_from(args)
    settings.validate()
    notifier = TelegramNotifier(
        os.getenv("TELEGRAM_BOT_TOKEN"),
        os.getenv("TELEGRAM_CHAT_ID"),
        os.getenv("TELEGRAM_PROXY_URL"),
    )
    await notifier.verify_identity(os.getenv("TELEGRAM_EXPECTED_BOT_USERNAME"))
    await notifier.verify_recipient()
    deduplicator = AlertDeduplicator(args.cooldown_seconds)
    print(
        "Telegram alert monitor started: "
        f"every {args.interval_seconds:g}s, cooldown {args.cooldown_seconds:g}s. "
        "Press Ctrl+C to stop."
    )
    while True:
        try:
            result = await SpreadScanner(settings).scan_once()
            now = datetime.now(UTC)
            selected = deduplicator.eligible(result.opportunities, now)[: args.max_alerts_per_scan]
            if selected:
                await notifier.send(selected)
                deduplicator.mark_sent(selected, now)
                print(f"Sent Telegram alert for {len(selected)} opportunity(ies).")
            else:
                print("No new qualifying opportunity.")
            for warning in result.warnings:
                print(f"warning: {warning}", file=sys.stderr)
        except (NotificationError, UpstreamApiError, httpx.HTTPError) as error:
            # Transient data-provider or Telegram errors must not stop a
            # monitor that is expected to run unattended.
            print(f"warning: {error}", file=sys.stderr)
        await asyncio.sleep(args.interval_seconds)


def main() -> None:
    load_local_env()
    try:
        raise SystemExit(asyncio.run(_run(_parse_args())))
    except (NotificationError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2) from error
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
