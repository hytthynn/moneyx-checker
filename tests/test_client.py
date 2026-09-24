from decimal import Decimal

import httpx
import pytest

from moneyx.client import (
    APIContractError,
    AuthenticationError,
    MoneyXClient,
    TransientAPIError,
)


@pytest.mark.asyncio
async def test_successful_auth_list_and_wallet():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/me"):
            return httpx.Response(200, json={"data": {"user": {"id": 7}}})
        if request.url.path.endswith("/list"):
            assert request.content == b""
            return httpx.Response(
                200,
                json={
                    "data": {
                        "items": [
                            {"currency": "USDT", "network": "TRC-20", "rate": "9.01"},
                            {"currency": "USDT", "network": "ERC-20"},
                        ]
                    }
                },
            )
        return httpx.Response(200, json={"data": {"wallet": {"rate": "9.02", "address": "x"}}})

    async with MoneyXClient(
        "https://example.com",
        token="secret",
        transport=httpx.MockTransport(handler),
    ) as client:
        auth = await client.authenticate()
        pairs = await client.list_crypto()
        rate = await client.wallet_rate(pairs[1])

    assert auth.user.id == 7
    assert [(p.currency, p.network) for p in pairs] == [
        ("USDT", "TRC-20"),
        ("USDT", "ERC-20"),
    ]
    assert rate == Decimal("9.02")
    assert requests[0].headers["authorization"] == "Bearer secret"
    assert requests[0].headers["x-client-currency"] == "RUB"


@pytest.mark.asyncio
async def test_mxi_cookie_is_used_only_after_bearer_fails():
    seen_cookies: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        cookie = request.headers.get("cookie")
        seen_cookies.append(cookie)
        if request.url.path.endswith("/me") and not cookie:
            return httpx.Response(401, json={})
        if request.url.path.endswith("/me"):
            return httpx.Response(200, json={"data": {"user": {"id": 1}}})
        return httpx.Response(
            200, json={"data": [{"currency": "BTC", "network": "Bitcoin", "rate": 1}]}
        )

    async with MoneyXClient(
        "https://example.com",
        token="token",
        mxi_token="compat",
        transport=httpx.MockTransport(handler),
    ) as client:
        await client.authenticate()
        await client.list_crypto()

    assert seen_cookies == [None, "mxi_token=compat", "mxi_token=compat"]


@pytest.mark.asyncio
async def test_expired_token_is_not_retried_without_mxi():
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(401, json={})

    async with MoneyXClient(
        "https://example.com", token="bad", transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(AuthenticationError):
            await client.authenticate()
    assert calls == 1


@pytest.mark.asyncio
async def test_schema_change_is_reported():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/me"):
            return httpx.Response(200, json={"data": {"user": {}}})
        return httpx.Response(200, json={"unexpected": True})

    async with MoneyXClient(
        "https://example.com", token="x", transport=httpx.MockTransport(handler)
    ) as client:
        await client.authenticate()
        with pytest.raises(APIContractError):
            await client.list_crypto()


@pytest.mark.asyncio
async def test_429_is_retried_with_bounded_attempts(monkeypatch):
    calls = 0

    async def no_sleep(_):
        return None

    monkeypatch.setattr("moneyx.client.asyncio.sleep", no_sleep)

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls < 3:
            return httpx.Response(429, json={})
        return httpx.Response(200, json={"data": {"user": {"id": 1}}})

    async with MoneyXClient(
        "https://example.com", token="x", transport=httpx.MockTransport(handler)
    ) as client:
        await client.authenticate()
    assert calls == 3


@pytest.mark.asyncio
async def test_timeout_stops_after_three_attempts(monkeypatch):
    calls = 0

    async def no_sleep(_):
        return None

    monkeypatch.setattr("moneyx.client.asyncio.sleep", no_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("timeout", request=request)

    async with MoneyXClient(
        "https://example.com", token="x", transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(TransientAPIError):
            await client.authenticate()
    assert calls == 3
