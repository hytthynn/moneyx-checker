from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from html import escape
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import ReplyParameters

from moneyx.models import RateBatch

MSK = ZoneInfo("Europe/Moscow")
TELEGRAM_LIMIT = 4096
RATE_STEP = Decimal("0.001")


def visible_rate(value: Decimal) -> Decimal:
    return value.quantize(RATE_STEP, rounding=ROUND_HALF_UP)


def format_rate(value: Decimal) -> str:
    return f"{visible_rate(value):f}"


def _pair_html(currency: str, network: str) -> str:
    return f"<b>{escape(currency)}</b> ({escape(network)})"


def _host(web_url: str) -> str:
    return urlparse(web_url).hostname or web_url


def main_message_html(batch: RateBatch, web_url: str, now: datetime | None = None) -> str:
    """Pinned-style summary with every rate; edited in place on each run."""
    local_now = (now or datetime.now(MSK)).astimezone(MSK)
    lines = [
        f"{_pair_html(rate.currency, rate.network)} — <code>{format_rate(rate.rate)}</code> ₽"
        for rate in batch.rates
    ]
    if batch.failures:
        lines.extend(["", "⚠️ <i>Не удалось получить:</i>"])
        lines.extend(_pair_html(item.currency, item.network) for item in batch.failures)
    lines.extend(
        [
            "",
            f"🌐 Домен: <b>{escape(_host(web_url))}</b>",
            f"🕒 Обновлено: <b>{local_now:%H:%M:%S}</b>",
        ]
    )
    return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class RateIncrease:
    currency: str
    network: str
    previous: Decimal
    current: Decimal


def find_increases(
    batch: RateBatch, previous: dict[tuple[str, str], Decimal]
) -> list[RateIncrease]:
    """Pairs whose visible (3-decimal) rate grew since the previous run."""
    result: list[RateIncrease] = []
    for rate in batch.rates:
        before = previous.get((rate.currency, rate.network))
        if before is None:
            continue
        old, new = visible_rate(before), visible_rate(rate.rate)
        if new > old:
            result.append(RateIncrease(rate.currency, rate.network, old, new))
    return result


def increases_html(increases: list[RateIncrease], now: datetime | None = None) -> list[str]:
    local_now = (now or datetime.now(MSK)).astimezone(MSK)
    lines = ["📈 <b>Курс вырос</b>", ""]
    lines.extend(
        f"{_pair_html(item.currency, item.network)} — <code>{item.current:f}</code> ₽"
        f" <i>(+{item.current - item.previous:f})</i>"
        for item in increases
    )
    lines.extend(["", f"🕒 {local_now:%d.%m.%Y %H:%M} MSK"])
    return split_lines(lines)


async def upsert_main_message(
    bot: Bot, chat_id: int, message_id: int | None, text: str
) -> tuple[int, bool]:
    """Edit the main message; send a new one if it is missing. Returns (id, created)."""
    if message_id is not None:
        try:
            await bot.edit_message_text(
                text=text,
                chat_id=chat_id,
                message_id=message_id,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
            return message_id, False
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc).lower():
                return message_id, False
            # Deleted or no longer editable: fall through and post a fresh one.
    sent = await bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
        disable_notification=True,
    )
    return sent.message_id, True


async def send_increases(bot: Bot, chat_id: int, reply_to: int, parts: list[str]) -> None:
    for part in parts:
        await bot.send_message(
            chat_id=chat_id,
            text=part,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_parameters=ReplyParameters(message_id=reply_to, allow_sending_without_reply=True),
        )


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
