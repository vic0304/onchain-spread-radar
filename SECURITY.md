# Security and privacy

This project is a read-only market scanner. It does not accept wallet private
keys, seed phrases, trading permissions, or withdrawal permissions.

## Keep local credentials private

Copy `.env.example` to `.env` and put local credentials only in `.env`.
`.env`, `.env.*`, virtual environments, and generated artifacts are ignored by
Git. Before committing, run `git status --ignored` and check that no local
configuration or logs are staged.

The optional credentials are sent only to their named provider over HTTPS:

- OKX API credentials: signed read-only market-data requests to OKX.
- GoPlus access token: contract tax/risk lookups to GoPlus.
- Telegram bot token and chat ID: bot identity checks and alert delivery to
  Telegram. The dashboard never returns them to the browser.

Use a dedicated Telegram bot and configure `TELEGRAM_EXPECTED_BOT_USERNAME`.
The alert process checks this name before it sends any message.

## Reporting a vulnerability

Do not post tokens, API keys, wallet secrets, or reproducible credential
details in a public issue. Open a private GitHub security advisory for this
repository instead. If a credential is exposed, revoke or rotate it first.
