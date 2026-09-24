from __future__ import annotations

from aiogram.types import Update
from fastapi import APIRouter, Header, HTTPException, Request

from runtime import get_runtime
from security import secure_equals

router = APIRouter()


@router.post("/api/telegram/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict[str, bool]:
    runtime = await get_runtime()
    if not secure_equals(x_telegram_bot_api_secret_token, runtime.config.telegram_webhook_secret):
        raise HTTPException(status_code=401, detail="unauthorized")
    try:
        payload = await request.json()
        update = Update.model_validate(payload, context={"bot": runtime.bot})
    except Exception as exc:
        raise HTTPException(status_code=400, detail="invalid update") from exc
    await runtime.dispatcher.feed_update(runtime.bot, update)
    return {"ok": True}
