"""Send one explicit, user-requested Telegram delivery test."""

from __future__ import annotations

import asyncio
import os
import sys

from .config import load_local_env
from .errors import NotificationError
from .telegram import TelegramNotifier


async def _run() -> None:
    notifier = TelegramNotifier(
        os.getenv("TELEGRAM_BOT_TOKEN"),
        os.getenv("TELEGRAM_CHAT_ID"),
        os.getenv("TELEGRAM_PROXY_URL"),
    )
    await notifier.verify_identity(os.getenv("TELEGRAM_EXPECTED_BOT_USERNAME"))
    await notifier.verify_recipient()
    await notifier.send_text(
        "✅ 链上 / CEX 价差雷达测试成功。\n"
        "后台监控已连接；实际提醒仅在满足你的净价差阈值时发送。"
    )


def main() -> None:
    load_local_env()
    try:
        asyncio.run(_run())
    except (NotificationError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2) from error
    print("Telegram test message sent.")


if __name__ == "__main__":
    main()
