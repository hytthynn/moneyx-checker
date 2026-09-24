from __future__ import annotations

import asyncio
from decimal import Decimal

from moneyx.client import MoneyXClient
from moneyx.models import CryptoPair, RateBatch, RateFailure, RateResult, as_decimal


def deduplicate_pairs(pairs: list[CryptoPair]) -> list[CryptoPair]:
    result: list[CryptoPair] = []
    seen: set[tuple[str, str]] = set()
    for pair in pairs:
        key = (pair.currency, pair.network)
        if key not in seen:
            seen.add(key)
            result.append(pair)
    return result


def normalize_rate(raw: Decimal | str | int | float) -> Decimal:
    return as_decimal(raw)


async def collect_rates(
    client: MoneyXClient,
    *,
    max_concurrency: int = 3,
) -> RateBatch:
    await client.authenticate()
    pairs = deduplicate_pairs(await client.list_crypto())
    results: list[RateResult | None] = [None] * len(pairs)
    failures: list[RateFailure | None] = [None] * len(pairs)
    semaphore = asyncio.Semaphore(max_concurrency)

    async def resolve(index: int, pair: CryptoPair) -> None:
        source = "list"
        try:
            raw = pair.rate
            if raw is None:
                source = "wallet"
                async with semaphore:
                    raw = await client.wallet_rate(pair)
            results[index] = RateResult(
                currency=pair.currency,
                network=pair.network,
                rate=normalize_rate(raw),
                source=source,
            )
        except Exception as exc:  # partial results are an explicit product feature
            failures[index] = RateFailure(
                currency=pair.currency, network=pair.network, error=str(exc)[:300]
            )

    await asyncio.gather(*(resolve(i, pair) for i, pair in enumerate(pairs)))
    return RateBatch(
        rates=[item for item in results if item is not None],
        failures=[item for item in failures if item is not None],
    )
