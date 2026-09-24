from decimal import Decimal

import pytest

from moneyx.models import AuthResult, AuthUser, CryptoPair
from moneyx.rates import collect_rates, deduplicate_pairs, normalize_rate


class FakeClient:
    async def authenticate(self):
        return AuthResult(user=AuthUser(id=1))

    async def list_crypto(self):
        return [
            CryptoPair(currency="USDT", network="TRC", rate=Decimal("9")),
            CryptoPair(currency="USDT", network="TRC", rate=Decimal("9")),
            CryptoPair(currency="USDT", network="ERC", rate=None),
            CryptoPair(currency="BTC", network="Bitcoin", rate=None),
        ]

    async def wallet_rate(self, pair):
        if pair.currency == "BTC":
            raise RuntimeError("temporary sample failure")
        return Decimal("8.5")


def test_exact_pair_deduplication_preserves_networks_and_order():
    pairs = [
        CryptoPair(currency="USDT", network="TRC"),
        CryptoPair(currency="USDT", network="TRC"),
        CryptoPair(currency="USDT", network="ERC"),
    ]
    assert [(p.currency, p.network) for p in deduplicate_pairs(pairs)] == [
        ("USDT", "TRC"),
        ("USDT", "ERC"),
    ]


def test_rate_is_used_without_multiplier():
    assert normalize_rate("90.167") == Decimal("90.167")


@pytest.mark.asyncio
async def test_list_rate_first_and_partial_wallet_failures():
    batch = await collect_rates(FakeClient())  # type: ignore[arg-type]
    assert [(r.network, r.rate, r.source) for r in batch.rates] == [
        ("TRC", Decimal("9"), "list"),
        ("ERC", Decimal("8.5"), "wallet"),
    ]
    assert [(f.currency, f.network) for f in batch.failures] == [("BTC", "Bitcoin")]
