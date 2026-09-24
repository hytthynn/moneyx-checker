from __future__ import annotations

import logging
from datetime import UTC

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import BaseFilter, Command, CommandObject
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from config import Settings
from moneyx.client import AuthenticationError, MoneyXClient, discover_domain
from security import mask_secret, redact
from services.jobs import JobService
from storage.repository import Repository, SecretValues

logger = logging.getLogger(__name__)

HELP = """Money-X checker

/status — состояние
/check — проверить курсы сейчас
/domain, /domain_set <host> — домен
/auth_set, /auth_status, /auth_clear — авторизация"""


class IsAdmin(BaseFilter):
    def __init__(self, admin_id: int):
        self.admin_id = admin_id

    async def __call__(self, event: Message | CallbackQuery) -> bool:
        return bool(event.from_user and event.from_user.id == self.admin_id)


def confirmation(action: str, label: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=label, callback_data=f"confirm:{action}"),
                InlineKeyboardButton(text="Отмена", callback_data="confirm:cancel"),
            ]
        ]
    )


async def effective_secrets(repository: Repository) -> SecretValues:
    return await repository.get_secrets()


async def validate_auth(
    repository: Repository,
    config: Settings,
    token: str,
    mxi_token: str | None,
) -> None:
    settings = await repository.get_settings()
    async with MoneyXClient(
        settings.web_url,
        settings.api_url,
        token=token,
        mxi_token=mxi_token,
        timeout=config.moneyx_timeout_seconds,
    ) as client:
        await client.authenticate()
        await client.list_crypto()


def build_router(repository: Repository, config: Settings, jobs: JobService) -> Router:
    router = Router(name="commands")
    router.message.filter(F.chat.type == ChatType.PRIVATE)
    admin = IsAdmin(config.admin_telegram_id)

    @router.message(Command("start", "help"), admin)
    async def help_command(message: Message) -> None:
        await message.answer(HELP)

    @router.message(Command("status"), admin)
    async def status_command(message: Message) -> None:
        state = await repository.get_settings()
        secrets = await effective_secrets(repository)
        last = (
            state.last_check_at.astimezone(UTC).strftime("%d.%m.%Y %H:%M UTC")
            if state.last_check_at
            else "никогда"
        )
        await message.answer(
            "\n".join(
                [
                    f"Домен: {state.web_url}",
                    f"API: {state.api_url}",
                    f"Группа: {config.telegram_group_id}",
                    "Расписание: каждый час в HH:02 MSK",
                    "Рассылка: активна",
                    f"Авторизация: {'валидна' if secrets.auth_valid and secrets.token else 'не задана/истекла'} (encrypted database)",
                    f"Токен: {mask_secret(secrets.token)}",
                    f"Последняя ручная проверка: {last}",
                ]
            )
        )

    @router.message(Command("check"), admin)
    async def check_command(message: Message) -> None:
        await message.answer("Проверяю курсы…")
        result = await jobs.run_manual()
        await message.answer(result.detail)

    @router.message(Command("domain"), admin)
    async def domain_command(message: Message) -> None:
        state = await repository.get_settings()
        await message.answer(f"Текущий домен: {state.web_url}\nAPI: {state.api_url}")

    @router.message(Command("domain_set"), admin)
    async def domain_set(message: Message, command: CommandObject) -> None:
        if not command.args:
            await message.answer("Использование: /domain_set <hostname или HTTPS URL>")
            return
        await message.answer("Проверяю домен и текущую авторизацию…")
        secrets = await effective_secrets(repository)
        try:
            pair = await discover_domain(
                command.args,
                token=secrets.token,
                mxi_token=secrets.mxi_token,
                timeout=config.moneyx_timeout_seconds,
                allowed_api_hosts=set(),
            )
            await repository.set_domain(pair.web_url, pair.api_url)
            await message.answer(f"Домен сохранён: {pair.web_url}\nAPI: {pair.api_url}")
        except AuthenticationError:
            await message.answer(
                "Новый домен доступен, но текущая авторизация не подошла. Старый домен сохранён; выполните /auth_set и повторите смену."
            )
        except Exception as exc:
            logger.info("Domain validation failed: %s", redact(exc))
            await message.answer(f"Домен не изменён: {redact(exc)[:300]}")

    @router.message(Command("auth_set"), admin)
    async def auth_set(message: Message) -> None:
        if message.chat.type != ChatType.PRIVATE:
            await message.answer("Авторизацию можно обновить только в личном чате с ботом.")
            return
        await repository.put_pending(config.admin_telegram_id, "auth_token")
        await message.answer(
            "Отправьте token следующим сообщением. Я сразу удалю сообщение и не буду повторять значение. Операция истечёт через 10 минут."
        )

    @router.message(Command("auth_status"), admin)
    async def auth_status(message: Message) -> None:
        secrets = await effective_secrets(repository)
        if not secrets.token:
            await message.answer("Авторизация не настроена.")
            return
        try:
            await validate_auth(repository, config, secrets.token, secrets.mxi_token)
            await repository.save_secrets(secrets.token, secrets.mxi_token, valid=True)
            await repository.reset_alert("auth_expired")
            await message.answer(f"Сессия действительна. Токен: {mask_secret(secrets.token)}")
        except Exception as exc:
            await repository.mark_auth_invalid(secrets.token)
            await message.answer(f"Сессия не прошла проверку: {redact(exc)[:300]}")

    @router.message(Command("auth_clear"), admin)
    async def auth_clear(message: Message) -> None:
        await message.answer(
            "Удалить сохранённую авторизацию?",
            reply_markup=confirmation("auth_clear", "Удалить авторизацию"),
        )

    @router.callback_query(F.data.startswith("confirm:"), admin)
    async def confirm(callback: CallbackQuery) -> None:
        action = callback.data.split(":", 1)[1] if callback.data else "cancel"
        if action == "auth_clear":
            await repository.clear_secrets()
            text = "Авторизация удалена."
        else:
            text = "Отменено."
        await callback.answer()
        if callback.message:
            await callback.message.edit_text(text)

    @router.message(F.chat.type == ChatType.PRIVATE, admin, F.text, ~F.text.startswith("/"))
    async def secret_input(message: Message) -> None:
        pending = await repository.pop_pending(config.admin_telegram_id)
        if pending is None or not message.text:
            return
        try:
            await message.delete()
        except TelegramAPIError:
            logger.warning("Could not delete an admin secret message")
        value = message.text.strip()
        if not value or len(value) > 8192:
            await message.answer("Секрет пуст или слишком длинный. Начните заново: /auth_set")
            return
        if pending.action == "auth_token":
            try:
                await validate_auth(repository, config, value, None)
                await repository.save_secrets(value, None)
                await message.answer("Авторизация проверена и сохранена в зашифрованном виде.")
            except AuthenticationError:
                await repository.put_pending(config.admin_telegram_id, "auth_mxi", payload=value)
                await message.answer(
                    "Одного token недостаточно. Отправьте mxi_token следующим сообщением; оно также будет удалено."
                )
            except Exception as exc:
                await message.answer(f"Авторизация не сохранена: {redact(exc)[:300]}")
        elif pending.action == "auth_mxi" and pending.payload:
            try:
                await validate_auth(repository, config, pending.payload, value)
                await repository.save_secrets(pending.payload, value)
                await message.answer("Авторизация с mxi_token проверена и сохранена.")
            except Exception as exc:
                await message.answer(f"Авторизация не сохранена: {redact(exc)[:300]}")

    @router.message(F.text.startswith("/"))
    async def denied(message: Message) -> None:
        await message.answer("Команда недоступна.")

    @router.message()
    async def denied_message(message: Message) -> None:
        if not message.from_user or message.from_user.id != config.admin_telegram_id:
            await message.answer("Команда недоступна.")

    @router.callback_query()
    async def denied_callback(callback: CallbackQuery) -> None:
        await callback.answer("Недоступно")

    return router
