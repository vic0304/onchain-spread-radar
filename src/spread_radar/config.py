"""Local, non-secret configuration helpers."""

from pathlib import Path

from dotenv import load_dotenv


def load_local_env() -> None:
    """Load the ignored project-local .env with project-local precedence.

    Desktop shells can inherit generic names such as TELEGRAM_BOT_TOKEN from
    a different project.  This radar must use its own ignored .env rather than
    silently borrowing that other Bot's identity. The file is never returned,
    logged or sent to the browser.
    """

    project_root = Path(__file__).resolve().parents[2]
    load_dotenv(project_root / ".env", override=True)
