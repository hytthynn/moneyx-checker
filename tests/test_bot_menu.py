from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot, Dispatcher
from aiogram.methods import DeleteMessage, EditMessageText, SendMessage
from aiogram.types import Update

from bot import handlers
from config import Settings
from moneyx.client import AuthenticationError
from moneyx.domain import DomainPair
from services.jobs import JobResult
from storage.repository import PendingValue, SecretValues

ADMIN_ID = 123456
PANEL_ID = 10


class MenuRepository:
    def __init__(self):
        self.pending = None
        self.domain = None
        self.secrets = SecretValues("old-token", None, True, None)

    async def clear_pending(self, admin_id):
        self.pending = None

    async def put_pending(self, admin_id, action, payload):
        self.pending = PendingValue(action, payload)

    async def pop_pending(self, admin_id):
        value, self.pending = self.pending, None
        return value

    async def get_settings(self):
        return SimpleNamespace(web_url="https://old.example", api_url="https://api.old.example")

    async def get_secrets(self):
        return self.secrets

    async def set_domain(self, web_url, api_url):
        self.domain = (web_url, api_url)

    async def save_secrets(self, token, mxi_token):
        self.secrets = SecretValues(token, mxi_token, True, None)


def message_update(update_id, message_id, text):
    return Update.model_validate(
        {
            "update_id": update_id,
            "message": {
                "message_id": message_id,
                "date": 0,
                "chat": {"id": ADMIN_ID, "type": "private", "first_name": "Admin"},
                "from": {"id": ADMIN_ID, "is_bot": False, "first_name": "Admin"},
                "text": text,
                **(
                    {"entities": [{"type": "bot_command", "offset": 0, "length": 6}]}
                    if text == "/start" else {}
                ),
            },
        }
    )


def callback_update(update_id, data):
    return Update.model_validate(
        {
            "update_id": update_id,
            "callback_query": {
                "id": str(update_id),
                "from": {"id": ADMIN_ID, "is_bot": False, "first_name": "Admin"},
                "chat_instance": "test",
                "data": data,
                "message": {
                    "message_id": PANEL_ID,
                    "date": 0,
                    "chat": {"id": ADMIN_ID, "type": "private", "first_name": "Admin"},
                    "text": "menu",
                },
            },
        }
    )


def group_message_update(update_id, chat_id, *, is_bot=False, anonymous=False):
    return Update.model_validate(
        {
            "update_id": update_id,
            "message": {
                "message_id": update_id,
                "date": 0,
                "chat": {"id": chat_id, "type": "supergroup", "title": "Rates"},
                "from": {"id": 1087968824 if anonymous else 42, "is_bot": is_bot, "first_name": "User"},
                "text": "hello",
                **(
                    {"sender_chat": {"id": chat_id, "type": "supergroup", "title": "Rates"}}
                    if anonymous else {}
                ),
            },
        }
    )


@pytest.mark.asyncio
async def test_group_user_messages_are_deleted_only_in_configured_group(monkeypatch):
    bot = Bot("123456:ABC")
    telegram_call = AsyncMock()
    monkeypatch.setattr(Bot, "__call__", telegram_call)
    dispatcher = Dispatcher()
    dispatcher.include_router(
        handlers.build_router(
            MenuRepository(),
            Settings(admin_telegram_id=ADMIN_ID, telegram_group_id=-10042),
            SimpleNamespace(run_manual=AsyncMock()),
        )
    )
    try:
        await dispatcher.feed_update(bot, group_message_update(1, -10042))
        await dispatcher.feed_update(bot, group_message_update(2, -10042, is_bot=True))
        await dispatcher.feed_update(bot, group_message_update(3, -10042, is_bot=True, anonymous=True))
        await dispatcher.feed_update(bot, group_message_update(4, -10099))
        deleted = [
            call.args[-1] for call in telegram_call.await_args_list
            if isinstance(call.args[-1], DeleteMessage)
        ]
        assert [(method.chat_id, method.message_id) for method in deleted] == [
            (-10042, 1), (-10042, 3)
        ]
    finally:
        await bot.session.close()


@pytest.mark.asyncio
async def test_menu_edits_one_panel_and_removes_domain_input(monkeypatch):
    repository = MenuRepository()
    jobs = SimpleNamespace(run_manual=AsyncMock(return_value=JobResult("sent", "ok")))
    bot = Bot("123456:ABC")
    telegram_call = AsyncMock()
    monkeypatch.setattr(Bot, "__call__", telegram_call)
    discover = AsyncMock(return_value=DomainPair("https://new.example", "https://api.new.example"))
    monkeypatch.setattr(handlers, "discover_domain", discover)
    dispatcher = Dispatcher()
    dispatcher.include_router(handlers.build_router(repository, Settings(admin_telegram_id=ADMIN_ID), jobs))
    try:
        await dispatcher.feed_update(bot, message_update(1, 1, "/start"))
        await dispatcher.feed_update(bot, callback_update(2, "menu:domain"))
        await dispatcher.feed_update(bot, message_update(3, 2, "new.example"))
        methods = [call.args[-1] for call in telegram_call.await_args_list]
        assert sum(isinstance(method, SendMessage) for method in methods) == 1
        assert sum(isinstance(method, DeleteMessage) for method in methods) == 2
        assert repository.domain == ("https://new.example", "https://api.new.example")
        edits = [method for method in methods if isinstance(method, EditMessageText)]
        assert all(method.message_id == PANEL_ID for method in edits)
        assert "✅ Домен изменён" in edits[-1].text
        await dispatcher.feed_update(bot, callback_update(4, "menu:check"))
        jobs.run_manual.assert_awaited_once()
        methods = [call.args[-1] for call in telegram_call.await_args_list]
        assert sum(isinstance(method, SendMessage) for method in methods) == 1
    finally:
        await bot.session.close()


@pytest.mark.asyncio
async def test_cancelled_auth_input_is_deleted_without_saving(monkeypatch):
    repository = MenuRepository()
    jobs = SimpleNamespace(run_manual=AsyncMock())
    bot = Bot("123456:ABC")
    telegram_call = AsyncMock()
    monkeypatch.setattr(Bot, "__call__", telegram_call)
    dispatcher = Dispatcher()
    dispatcher.include_router(handlers.build_router(repository, Settings(admin_telegram_id=ADMIN_ID), jobs))
    try:
        await dispatcher.feed_update(bot, callback_update(1, "menu:auth"))
        await dispatcher.feed_update(bot, callback_update(2, "menu:cancel"))
        await dispatcher.feed_update(bot, message_update(3, 3, "sensitive-token"))
        methods = [call.args[-1] for call in telegram_call.await_args_list]
        assert sum(isinstance(method, DeleteMessage) for method in methods) == 1
        assert repository.secrets.token == "old-token"
        assert not any(isinstance(method, SendMessage) for method in methods)
    finally:
        await bot.session.close()


@pytest.mark.asyncio
async def test_authorization_fallback_uses_same_panel_and_hides_both_inputs(monkeypatch):
    repository = MenuRepository()
    bot = Bot("123456:ABC")
    telegram_call = AsyncMock()
    monkeypatch.setattr(Bot, "__call__", telegram_call)
    validate = AsyncMock(side_effect=[AuthenticationError("token insufficient"), None])
    monkeypatch.setattr(handlers, "validate_auth", validate)
    dispatcher = Dispatcher()
    dispatcher.include_router(
        handlers.build_router(
            repository, Settings(admin_telegram_id=ADMIN_ID),
            SimpleNamespace(run_manual=AsyncMock()),
        )
    )
    try:
        await dispatcher.feed_update(bot, callback_update(1, "menu:auth"))
        await dispatcher.feed_update(bot, message_update(2, 2, "new-token"))
        await dispatcher.feed_update(bot, message_update(3, 3, "mxi-cookie"))
        methods = [call.args[-1] for call in telegram_call.await_args_list]
        assert sum(isinstance(method, DeleteMessage) for method in methods) == 2
        assert not any(isinstance(method, SendMessage) for method in methods)
        edits = [method for method in methods if isinstance(method, EditMessageText)]
        assert all(method.message_id == PANEL_ID for method in edits)
        assert "✅ Авторизация изменена" in edits[-1].text
        assert "new-token" not in edits[-1].text
        assert "mxi-cookie" not in edits[-1].text
        assert repository.secrets.token == "new-token"
        assert repository.secrets.mxi_token == "mxi-cookie"
        assert validate.await_count == 2
    finally:
        await bot.session.close()


def test_status_escapes_external_values_and_exposes_no_token():
    html = handlers.status_text("https://a.example/<x>", "https://api.example/?a=1&b=2", True)
    assert "&lt;x&gt;" in html
    assert "&amp;" in html
    assert "Авторизация" in html
    assert "token" not in html
