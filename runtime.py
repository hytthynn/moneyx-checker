from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramAPIError, TelegramMigrateToChat

from bot.handlers import build_router
from config import Settings, get_settings
from security import RedactingFilter, SecretBox
from services.jobs import JobService
from storage.repository import Repository

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class Runtime:
    config: Settings
    repository: Repository
    bot: Bot
    dispatcher: Dispatcher
    jobs: JobService

    async def close(self) -> None:
        await self.bot.session.close()
        await self.repository.close()


_runtime: Runtime | None = None
_runtime_lock = asyncio.Lock()


def configure_logging() -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    for handler in handlers:
        handler.addFilter(RedactingFilter())
    logging.basicConfig(level="INFO", handlers=handlers, force=True)


async def resolve_group_migration(config: Settings, bot: Bot) -> Settings:
    """Use the current supergroup ID when Telegram migrated a basic group."""
    try:
        await bot.get_chat_administrators(config.telegram_group_id)
    except TelegramMigrateToChat as exc:
        logger.warning(
            "Telegram group migrated from %s to %s",
            config.telegram_group_id,
            exc.migrate_to_chat_id,
        )
        return config.model_copy(update={"telegram_group_id": exc.migrate_to_chat_id})
    except TelegramAPIError as exc:
        logger.warning("Could not check Telegram group migration: %s", type(exc).__name__)
    return config


async def get_runtime() -> Runtime:
    global _runtime
    if _runtime is not None:
        return _runtime
    async with _runtime_lock:
        if _runtime is not None:
            return _runtime
        config = get_settings()
        missing = config.validate_runtime()
        if missing:
            raise RuntimeError(f"Missing required configuration: {', '.join(missing)}")
        configure_logging()
        repository = Repository(config.database_url, SecretBox(config.app_encryption_key))
        bot = Bot(config.telegram_bot_token, default=DefaultBotProperties(parse_mode=None))
        config = await resolve_group_migration(config, bot)
        jobs = JobService(repository, bot, config)
        dispatcher = Dispatcher()
        dispatcher.include_router(build_router(repository, config, jobs))
        _runtime = Runtime(config, repository, bot, dispatcher, jobs)
        return _runtime
