from __future__ import annotations

import json
import logging
from decimal import Decimal, InvalidOperation
from html import escape

from aiogram import F, Router
from aiogram.enums import ChatType, ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import BaseFilter, Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import Settings
from moneyx.client import AuthenticationError, MoneyXClient, discover_domain
from moneyx.domain import DomainValidationError
from security import redact
from services.jobs import JobService
from storage.repository import PendingValue, Repository

logger = logging.getLogger(__name__)


class IsAdmin(BaseFilter):
    def __init__(self, admin_id: int):
        self.admin_id = admin_id

    async def __call__(self, event: Message | CallbackQuery) -> bool:
        return bool(event.from_user and event.from_user.id == self.admin_id)


def keyboard(*rows: tuple[tuple[str, str], ...]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=data) for label, data in row]
            for row in rows
        ]
    )


MAIN_KEYBOARD = keyboard(
    (("📊 Статус", "menu:status"), ("🔄 Проверить курс", "menu:check")),
    (("🌐 Изменить домен", "menu:domain"),),
    (("🔐 Изменить авторизацию", "menu:auth"),),
    (("📈 Порог роста", "menu:threshold"),),
)
BACK_KEYBOARD = keyboard((("← В меню", "menu:home"),))
CANCEL_KEYBOARD = keyboard((("✕ Отмена", "menu:cancel"),))


def main_text() -> str:
    return (
        "<b>⚙️ Money-X · Управление</b>\n"
        "<i>Проверка курсов и настройки бота</i>\n\n"
        "Выберите действие ниже."
    )


def status_text(web_url: str, api_url: str, auth_valid: bool) -> str:
    auth = "✅ Активна" if auth_valid else "⚠️ Не настроена или истекла"
    return (
        "<b>📊 Статус</b>\n\n"
        f"🌐 <b>Домен</b>\n<code>{escape(web_url)}</code>\n\n"
        f"🔗 <b>API</b>\n<code>{escape(api_url)}</code>\n\n"
        f"🔐 <b>Авторизация</b>\n{auth}"
    )


def result_text(title: str, detail: str) -> str:
    return f"<b>{title}</b>\n\n{escape(detail)}"


def format_percent(value: Decimal) -> str:
    return f"{value.normalize():f}%"


def parse_threshold_percent(raw: str) -> Decimal:
    value = raw.strip().removesuffix("%").strip().replace(",", ".")
    try:
        percent = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("Укажите число, например 0.5 или 0,5%.") from exc
    if not percent.is_finite() or not Decimal("0") <= percent <= Decimal("100"):
        raise ValueError("Порог должен быть от 0 до 100%.")
    if percent != percent.quantize(Decimal("0.01")):
        raise ValueError("Допустимо не более двух знаков после запятой.")
    return percent


def pending_payload(message_id: int, token: str | None = None) -> str:
    return json.dumps({"message_id": message_id, "token": token})


def parse_pending(value: PendingValue) -> tuple[int, str | None]:
    try:
        payload = json.loads(value.payload or "")
        message_id = payload["message_id"]
        token = payload.get("token")
        if not isinstance(message_id, int) or isinstance(message_id, bool):
            raise ValueError("invalid message id")
        if token is not None and not isinstance(token, str):
            raise ValueError("invalid token")
        return message_id, token
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError("invalid pending action") from exc


async def validate_auth(repository: Repository, config: Settings, token: str, mxi_token: str | None) -> None:
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


async def edit_message(message: Message, text: str, markup: InlineKeyboardMarkup) -> None:
    try:
        await message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise


async def edit_panel(
    message: Message, panel_message_id: int, text: str, markup: InlineKeyboardMarkup
) -> None:
    await message.bot.edit_message_text(
        chat_id=message.chat.id,
        message_id=panel_message_id,
        text=text,
        parse_mode=ParseMode.HTML,
        reply_markup=markup,
    )


def build_router(repository: Repository, config: Settings, jobs: JobService) -> Router:
    router = Router(name="bot")
    private_router = Router(name="admin_menu")
    private_router.message.filter(F.chat.type == ChatType.PRIVATE)
    private_router.callback_query.filter(F.message.chat.type == ChatType.PRIVATE)
    admin = IsAdmin(config.admin_telegram_id)

    @router.message(F.chat.id == config.telegram_group_id)
    async def delete_user_message(message: Message) -> None:
        from_user = message.from_user
        anonymous_admin = bool(
            message.sender_chat and message.sender_chat.id == message.chat.id
        )
        if not anonymous_admin and (not from_user or from_user.is_bot):
            return
        try:
            await message.delete()
        except TelegramAPIError as exc:
            logger.warning("Could not delete a user message in the group: %s", redact(exc))

    @private_router.message(Command("start"), admin)
    async def start(message: Message) -> None:
        await repository.clear_pending(config.admin_telegram_id)
        await message.answer(main_text(), parse_mode=ParseMode.HTML, reply_markup=MAIN_KEYBOARD)
        try:
            await message.delete()
        except TelegramAPIError:
            logger.warning("Could not delete the /start message")

    @private_router.callback_query(F.data == "menu:home", admin)
    async def home(callback: CallbackQuery) -> None:
        await repository.clear_pending(config.admin_telegram_id)
        await callback.answer()
        if isinstance(callback.message, Message):
            await edit_message(callback.message, main_text(), MAIN_KEYBOARD)

    @private_router.callback_query(F.data == "menu:status", admin)
    async def status(callback: CallbackQuery) -> None:
        await callback.answer()
        if not isinstance(callback.message, Message):
            return
        state = await repository.get_settings()
        secrets = await repository.get_secrets()
        await edit_message(
            callback.message,
            status_text(state.web_url, state.api_url, bool(secrets.token and secrets.auth_valid)),
            BACK_KEYBOARD,
        )

    @private_router.callback_query(F.data == "menu:check", admin)
    async def check(callback: CallbackQuery) -> None:
        await callback.answer()
        result = await jobs.run_manual()
        if result.status != "sent":
            logger.info("Manual rate check did not send: %s", redact(result.detail))

    @private_router.callback_query(F.data == "menu:domain", admin)
    async def domain(callback: CallbackQuery) -> None:
        if not isinstance(callback.message, Message):
            await callback.answer()
            return
        await repository.put_pending(
            config.admin_telegram_id, "domain", payload=pending_payload(callback.message.message_id)
        )
        await callback.answer()
        await edit_message(
            callback.message,
            "<b>🌐 Изменить домен</b>\n\n"
            "Отправьте новый домен или HTTPS URL следующим сообщением. "
            "Сообщение будет удалено после получения.\n\n"
            "<i>Ожидание ввода: 10 минут.</i>",
            CANCEL_KEYBOARD,
        )

    @private_router.callback_query(F.data == "menu:auth", admin)
    async def auth(callback: CallbackQuery) -> None:
        if not isinstance(callback.message, Message):
            await callback.answer()
            return
        await repository.put_pending(
            config.admin_telegram_id, "auth_token", payload=pending_payload(callback.message.message_id)
        )
        await callback.answer()
        await edit_message(
            callback.message,
            "<b>🔐 Изменить авторизацию</b>\n\n"
            "Отправьте <code>token</code> следующим сообщением. "
            "Я удалю его после получения и проверю доступ к Money-X.\n\n"
            "<i>Ожидание ввода: 10 минут.</i>",
            CANCEL_KEYBOARD,
        )

    @private_router.callback_query(F.data == "menu:threshold", admin)
    async def threshold(callback: CallbackQuery) -> None:
        if not isinstance(callback.message, Message):
            await callback.answer()
            return
        state = await repository.get_settings()
        await repository.put_pending(
            config.admin_telegram_id,
            "threshold",
            payload=pending_payload(callback.message.message_id),
        )
        await callback.answer()
        await edit_message(
            callback.message,
            "<b>📈 Порог роста курса</b>\n\n"
            f"Текущий порог: <b>{format_percent(state.increase_threshold_percent)}</b>\n\n"
            "Отправьте новый порог, например <code>0.5%</code>. "
            "В уведомления попадут монеты с ростом от этого значения. "
            "Отправленное сообщение будет удалено.\n\n"
            "<i>От 0 до 100%, не более двух знаков после запятой. "
            "Ожидание ввода: 10 минут.</i>",
            CANCEL_KEYBOARD,
        )

    @private_router.callback_query(F.data == "menu:cancel", admin)
    async def cancel(callback: CallbackQuery) -> None:
        await repository.clear_pending(config.admin_telegram_id)
        await callback.answer("Ввод отменён")
        if isinstance(callback.message, Message):
            await edit_message(callback.message, main_text(), MAIN_KEYBOARD)

    @private_router.message(admin)
    async def input_message(message: Message) -> None:
        pending = await repository.pop_pending(config.admin_telegram_id)
        try:
            await message.delete()
        except TelegramAPIError:
            logger.warning("Could not delete an admin input message")
        if pending is None:
            return
        try:
            panel_id, saved_token = parse_pending(pending)
        except ValueError:
            logger.warning("Invalid pending admin action")
            return
        value = (message.text or "").strip()
        if not value or len(value) > 8192:
            await edit_panel(
                message,
                panel_id,
                result_text("⚠️ Неверный ввод", "Отправьте текст длиной от 1 до 8192 символов."),
                BACK_KEYBOARD,
            )
            return

        if pending.action == "threshold":
            try:
                percent = parse_threshold_percent(value)
            except ValueError as exc:
                await repository.put_pending(
                    config.admin_telegram_id,
                    "threshold",
                    payload=pending_payload(panel_id),
                )
                await edit_panel(
                    message,
                    panel_id,
                    result_text("⚠️ Неверный порог", str(exc) + " Отправьте значение ещё раз."),
                    CANCEL_KEYBOARD,
                )
                return
            await repository.set_increase_threshold(percent)
            await edit_panel(
                message,
                panel_id,
                result_text(
                    "✅ Порог сохранён",
                    f"Уведомления о росте будут приходить от +{format_percent(percent)}.",
                ),
                BACK_KEYBOARD,
            )
            return

        if pending.action == "domain":
            secrets = await repository.get_secrets()
            try:
                pair = await discover_domain(
                    value,
                    token=secrets.token,
                    mxi_token=secrets.mxi_token,
                    timeout=config.moneyx_timeout_seconds,
                    allowed_api_hosts=set(),
                )
                await repository.set_domain(pair.web_url, pair.api_url)
                text = (
                    "<b>✅ Домен изменён</b>\n\n"
                    f"🌐 <code>{escape(pair.web_url)}</code>\n"
                    f"🔗 API: <code>{escape(pair.api_url)}</code>"
                )
            except AuthenticationError:
                text = result_text(
                    "⚠️ Домен не изменён",
                    "Текущая авторизация не подходит для нового домена. Обновите авторизацию и повторите попытку.",
                )
            except DomainValidationError as exc:
                text = result_text("⚠️ Домен не изменён", str(exc))
            except Exception as exc:
                logger.info("Domain validation failed: %s", type(exc).__name__)
                text = result_text("⚠️ Домен не изменён", "Не удалось проверить домен. Повторите попытку позже.")
            await edit_panel(message, panel_id, text, BACK_KEYBOARD)
            return

        if pending.action in ("auth_token", "auth_mxi"):
            token = saved_token if pending.action == "auth_mxi" else value
            mxi_token = value if pending.action == "auth_mxi" else None
            if not token:
                await edit_panel(
                    message, panel_id,
                    result_text("⚠️ Авторизация не изменена", "Срок ввода истёк. Начните заново."),
                    BACK_KEYBOARD,
                )
                return
            try:
                await validate_auth(repository, config, token, mxi_token)
                await repository.save_secrets(token, mxi_token)
                text = result_text("✅ Авторизация изменена", "Доступ к Money-X проверен и сохранён.")
                markup = BACK_KEYBOARD
            except AuthenticationError:
                if pending.action == "auth_token":
                    await repository.put_pending(
                        config.admin_telegram_id, "auth_mxi", payload=pending_payload(panel_id, token)
                    )
                    text = (
                        "<b>🔐 Нужен mxi_token</b>\n\n"
                        "Одного <code>token</code> недостаточно. Отправьте "
                        "<code>mxi_token</code> следующим сообщением. Оно будет удалено.\n\n"
                        "<i>Ожидание ввода: 10 минут.</i>"
                    )
                    markup = CANCEL_KEYBOARD
                else:
                    text = result_text(
                        "⚠️ Авторизация не изменена", "Money-X отклонил введённые данные."
                    )
                    markup = BACK_KEYBOARD
            except Exception as exc:
                logger.info("Authorization validation failed: %s", type(exc).__name__)
                text = result_text(
                    "⚠️ Авторизация не изменена", "Не удалось проверить авторизацию. Повторите попытку позже."
                )
                markup = BACK_KEYBOARD
            await edit_panel(message, panel_id, text, markup)

    @private_router.callback_query()
    async def denied_callback(callback: CallbackQuery) -> None:
        await callback.answer("Недоступно")

    router.include_router(private_router)
    return router
