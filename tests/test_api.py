from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

import api.hourly_cron as cron_module
import api.telegram_webhook as webhook_module


class FakeDispatcher:
    def __init__(self):
        self.calls = 0

    async def feed_update(self, bot, update):
        self.calls += 1


class FakeJobs:
    async def run_scheduled(self):
        return SimpleNamespace(status="sent", detail="minute=2", sent_parts=1)


@pytest.mark.asyncio
async def test_webhook_secret_is_required(monkeypatch):
    dispatcher = FakeDispatcher()
    runtime = SimpleNamespace(
        config=SimpleNamespace(telegram_webhook_secret="webhook-secret"),
        bot=SimpleNamespace(),
        dispatcher=dispatcher,
    )

    async def fake_runtime():
        return runtime

    monkeypatch.setattr(webhook_module, "get_runtime", fake_runtime)
    app = FastAPI()
    app.include_router(webhook_module.router)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        denied = await client.post("/api/telegram/webhook", json={"update_id": 1})
        accepted = await client.post(
            "/api/telegram/webhook",
            json={"update_id": 2},
            headers={"X-Telegram-Bot-Api-Secret-Token": "webhook-secret"},
        )
    assert denied.status_code == 401
    assert accepted.status_code == 200
    assert dispatcher.calls == 1


@pytest.mark.asyncio
async def test_cron_requires_bearer_and_accepts_external_secret(monkeypatch):
    runtime = SimpleNamespace(
        config=SimpleNamespace(cron_secret="cron"),
        jobs=FakeJobs(),
    )

    async def fake_runtime():
        return runtime

    monkeypatch.setattr(cron_module, "get_runtime", fake_runtime)
    app = FastAPI()
    app.include_router(cron_module.router)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        missing = await client.get("/api/hourly/01")
        denied = await client.get("/api/hourly/02")
        accepted = await client.get("/api/hourly/02", headers={"Authorization": "Bearer cron"})
    assert missing.status_code == 404
    assert denied.status_code == 401
    assert accepted.json()["status"] == "sent"
