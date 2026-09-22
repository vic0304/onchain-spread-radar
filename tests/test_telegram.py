from datetime import UTC, datetime, timedelta

import pytest

from spread_radar.alerts import AlertDeduplicator
from spread_radar.models import CexQuote, SpreadOpportunity, TokenCandidate
from spread_radar.telegram import TelegramNotifier, format_alert


def _opportunity() -> SpreadOpportunity:
    token = TokenCandidate("FOO", "0xAbC", "bsc", 1.0, 1_000_000, 500_000, "test")
    quote = CexQuote("gate", "FOO/USDT", "spot", 1.03, 1.04, 12_345, 8_765, 5, 5)
    return SpreadOpportunity(token, quote, 300, 150, 0, 150)


def test_alert_text_contains_the_executable_screen_and_risk_note() -> None:
    text = format_alert([_opportunity()])

    assert "FOO" in text
    assert "BNB Smart Chain（BSC · Chain ID 56）" in text
    assert "CEX 现货 买一 $1.03" in text
    assert "前 5 档买盘 $12,345" in text
    assert "净价差 1.50%" in text
    assert "自行核验" in text


def test_deduplicator_only_realerts_after_cooldown() -> None:
    item = _opportunity()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    deduplicator = AlertDeduplicator(cooldown_seconds=90)

    assert deduplicator.eligible((item,), now) == [item]
    deduplicator.mark_sent([item], now)
    assert deduplicator.eligible((item,), now + timedelta(seconds=89)) == []
    assert deduplicator.eligible((item,), now + timedelta(seconds=90)) == [item]


def test_telegram_notifier_needs_both_local_configuration_values() -> None:
    with pytest.raises(ValueError, match="TELEGRAM_BOT_TOKEN"):
        TelegramNotifier(None, "123")


@pytest.mark.anyio
async def test_identity_guard_requires_an_explicit_expected_username(monkeypatch) -> None:
    notifier = TelegramNotifier("token", "123")

    with pytest.raises(ValueError, match="TELEGRAM_EXPECTED_BOT_USERNAME"):
        await notifier.verify_identity(None)
