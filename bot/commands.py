from __future__ import annotations

from aiogram import Bot
from aiogram.types import BotCommand, BotCommandScopeAllPrivateChats, BotCommandScopeDefault


async def register_private_commands(bot: Bot) -> None:
    await bot.set_my_commands(
        [BotCommand(command="start", description="Открыть управление")],
        scope=BotCommandScopeAllPrivateChats(),
    )
    await bot.delete_my_commands(scope=BotCommandScopeDefault())
