from __future__ import annotations

from aiogram import Bot

from asyncio_compat import run
from config import get_settings


async def main() -> None:
    config = get_settings()
    if not config.app_base_url:
        raise SystemExit("APP_BASE_URL is required")
    async with Bot(config.telegram_bot_token) as bot:
        await bot.set_webhook(
            url=f"{config.app_base_url.rstrip('/')}/api/telegram/webhook",
            secret_token=config.telegram_webhook_secret,
            allowed_updates=["message", "callback_query"],
            drop_pending_updates=False,
        )
    print("Production webhook registered.")


if __name__ == "__main__":
    run(main())
