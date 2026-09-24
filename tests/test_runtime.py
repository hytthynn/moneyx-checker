from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramMigrateToChat
from aiogram.methods import GetChatAdministrators

from config import Settings
from runtime import resolve_group_migration


@pytest.mark.asyncio
async def test_migrated_group_id_is_used_for_runtime_config():
    old_id = -5576570645
    new_id = -1004403506237
    migration = TelegramMigrateToChat(
        method=GetChatAdministrators(chat_id=old_id),
        message="Group migrated",
        migrate_to_chat_id=new_id,
    )
    bot = SimpleNamespace(get_chat_administrators=AsyncMock(side_effect=migration))
    config = Settings(_env_file=None, telegram_group_id=old_id)

    resolved = await resolve_group_migration(config, bot)

    assert resolved.telegram_group_id == new_id
    assert config.telegram_group_id == old_id
    bot.get_chat_administrators.assert_awaited_once_with(old_id)
