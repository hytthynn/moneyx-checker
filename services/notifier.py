from __future__ import annotations

from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from aiogram import Bot

from moneyx.models import RateBatch

MSK = ZoneInfo("Europe/Moscow")
TELEGRAM_LIMIT = 4096


def format_rate(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.001'), rounding=ROUND_HALF_UP):f}"


def report_lines(batch: RateBatch, web_url: str, now: datetime | None = None) -> list[str]:
    local_now = (now or datetime.now(MSK)).astimezone(MSK)
    lines = [
        "Money-X — курсы пополнения",
        local_now.strftime("%d.%m.%Y %H:%M MSK"),
        "",
    ]
    lines.extend(
        f"{rate.currency} ({rate.network}) — {format_rate(rate.rate)} ₽" for rate in batch.rates
    )
    if batch.failures:
        lines.extend(["", "Не удалось получить:"])
        lines.extend(f"{item.currency} ({item.network})" for item in batch.failures)
    host = urlparse(web_url).hostname or web_url
    lines.extend(["", f"Домен: {host}", f"Обновлено: {local_now:%H:%M:%S}"])
    return lines


def split_lines(lines: list[str], limit: int = TELEGRAM_LIMIT) -> list[str]:
    if limit < 64:
        raise ValueError("message limit is too small")
    chunks: list[str] = []
    current: list[str] = []
    reserve = 24
    for line in lines:
        if len(line) > limit - reserve:
            line = line[: limit - reserve - 1] + "…"
        candidate = "\n".join([*current, line])
        if current and len(candidate) > limit - reserve:
            chunks.append("\n".join(current))
            current = [line]
        else:
            current.append(line)
    if current:
        chunks.append("\n".join(current))
    if len(chunks) == 1:
        return chunks
    total = len(chunks)
    return [f"Часть {index}/{total}\n{chunk}" for index, chunk in enumerate(chunks, 1)]


def format_report(batch: RateBatch, web_url: str, now: datetime | None = None) -> list[str]:
    return split_lines(report_lines(batch, web_url, now))


async def send_report(
    bot: Bot,
    chat_id: int,
    parts: list[str],
) -> None:
    for part in parts:
        await bot.send_message(
            chat_id=chat_id,
            text=part,
            disable_web_page_preview=True,
        )
