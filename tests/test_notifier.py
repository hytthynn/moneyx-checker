from decimal import Decimal

from moneyx.models import RateBatch, RateFailure, RateResult
from services.notifier import format_rate, format_report, split_lines


def test_rounding_matches_visible_three_decimals():
    assert format_rate(Decimal("90.1666")) == "90.167"


def test_report_contains_rates_and_failures_without_sensitive_details():
    batch = RateBatch(
        rates=[RateResult(currency="USDT", network="TRC-20", rate="90.167", source="list")],
        failures=[RateFailure(currency="BTC", network="Bitcoin", error="wallet address secret")],
    )
    text = "\n".join(format_report(batch, "https://mxc1n.com"))
    assert "USDT (TRC-20) — 90.167 ₽" in text
    assert "BTC (Bitcoin)" in text
    assert "wallet address secret" not in text


def test_long_messages_split_on_whole_lines_with_part_numbers():
    chunks = split_lines([f"row-{i}-" + "x" * 30 for i in range(20)], limit=120)
    assert len(chunks) > 1
    assert all(len(chunk) <= 120 for chunk in chunks)
    assert chunks[0].startswith("Часть 1/")
