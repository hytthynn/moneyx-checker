import re
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import EditMessageText

from moneyx.models import RateBatch, RateFailure, RateResult
from services.notifier import (
    MSK,
    find_increases,
    format_rate,
    increases_html,
    main_message_html,
    split_lines,
    upsert_main_message,
)

NOW = datetime(2026, 9, 24, 22, 2, 18, tzinfo=MSK)


def plain(html: str) -> str:
    return re.sub(r"<[^>]+>", "", html)


def rate(currency: str, network: str, value: str) -> RateResult:
    return RateResult(currency=currency, network=network, rate=value, source="list")


def test_rounding_matches_visible_three_decimals():
    assert format_rate(Decimal("90.1666")) == "90.167"


def test_main_message_matches_requested_layout():
    batch = RateBatch(rates=[rate("USDT", "TRC20", "90.1412"), rate("BTC", "BTC", "7612726.5")])
    html = main_message_html(batch, "https://mxc1o.com", NOW)

    assert "<b>USDT</b> (TRC20) — <code>90.141</code> ₽" in html
    lines = plain(html).splitlines()
    assert lines[0] == "USDT (TRC20) — 90.141 ₽"
    assert lines[1] == "BTC (BTC) — 7612726.500 ₽"
    assert "🌐 Домен: mxc1o.com" in lines
    assert "🕒 Обновлено: 22:02:18" in lines


def test_main_message_escapes_html_and_hides_failure_details():
    batch = RateBatch(
        rates=[rate("A<b>", "N&1", "1")],
        failures=[RateFailure(currency="BTC", network="Bitcoin", error="wallet address secret")],
    )
    html = main_message_html(batch, "https://mxc1n.com", NOW)
    assert "A&lt;b&gt;" in html and "N&amp;1" in html
    assert "BTC" in html
    assert "wallet address secret" not in html


def test_only_visible_increases_are_reported():
    batch = RateBatch(
        rates=[
            rate("TON", "TON", "128.415"),  # grew
            rate("USDT", "TRC20", "90.1412"),  # same after rounding
            rate("BTC", "BTC", "7612000"),  # fell
            rate("NEW", "NEW", "1"),  # no baseline
        ]
    )
    previous = {
        ("TON", "TON"): Decimal("127.2"),
        ("USDT", "TRC20"): Decimal("90.1409"),
        ("BTC", "BTC"): Decimal("7612726.5"),
    }
    increases = find_increases(batch, previous)
    assert [(i.currency, i.network) for i in increases] == [("TON", "TON")]

    text = plain("\n".join(increases_html(increases, NOW)))
    assert "TON (TON) — 128.415 ₽ (+1.215 · +0.96%)" in text
    assert "USDT" not in text


def test_percent_increase_is_unavailable_when_previous_rate_is_zero():
    batch = RateBatch(rates=[rate("TON", "TON", "1.000")])
    increases = find_increases(batch, {("TON", "TON"): Decimal("0")})

    assert "+1.000 · н/д" in plain("".join(increases_html(increases, NOW)))


@pytest.mark.asyncio
async def test_main_message_is_edited_in_place():
    bot = SimpleNamespace(edit_message_text=AsyncMock(), send_message=AsyncMock())
    assert await upsert_main_message(bot, -100, 42, "x") == (42, False)
    bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_deleted_main_message_is_recreated():
    error = TelegramBadRequest(EditMessageText(text="x"), "Bad Request: message to edit not found")
    bot = SimpleNamespace(
        edit_message_text=AsyncMock(side_effect=error),
        send_message=AsyncMock(return_value=SimpleNamespace(message_id=77)),
    )
    assert await upsert_main_message(bot, -100, 42, "x") == (77, True)


def test_long_messages_split_on_whole_lines_with_part_numbers():
    chunks = split_lines([f"row-{i}-" + "x" * 30 for i in range(20)], limit=120)
    assert len(chunks) > 1
    assert all(len(chunk) <= 120 for chunk in chunks)
    assert chunks[0].startswith("Часть 1/")
