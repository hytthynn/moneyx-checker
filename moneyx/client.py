from __future__ import annotations

import asyncio
import random
import re
from urllib.parse import urljoin
from collections.abc import Mapping
from typing import Any

import httpx
from pydantic import ValidationError

from moneyx.domain import (
    DomainPair,
    DomainValidationError,
    assert_public_dns,
    derive_api_url,
    parse_web_url,
)
from moneyx.models import AuthResult, AuthUser, CryptoPair, SchemaError, as_decimal


class MoneyXError(RuntimeError):
    pass


class AuthenticationError(MoneyXError):
    pass


class ForbiddenError(MoneyXError):
    pass


class TransientAPIError(MoneyXError):
    pass


class APIContractError(MoneyXError):
    pass


_API_URL_RE = re.compile(r"https://api\.[a-zA-Z0-9.-]+")
_CONFIG_API_RE = re.compile(r'["\'](?:apiBase|apiUrl|apiURL)["\']\s*:\s*["\'](https://[^"\']+)')
_RETRYABLE = {429, 500, 502, 503, 504}


class MoneyXClient:
    def __init__(
        self,
        web_url: str,
        api_url: str | None = None,
        *,
        token: str | None = None,
        mxi_token: str | None = None,
        timeout: float = 12.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.web_url = parse_web_url(web_url)
        self.api_url = parse_web_url(api_url or derive_api_url(self.web_url))
        self.token = token
        self.mxi_token = mxi_token
        self._use_mxi = False
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            follow_redirects=False,
            transport=transport,
            headers={
                "Accept": "application/json",
                "X-Client-Currency": "RUB",
                "Origin": self.web_url,
                "Referer": self.web_url + "/",
                "User-Agent": "MoneyX-Rate-Monitor/1.0 (+private Telegram bot)",
            },
        )

    async def __aenter__(self) -> "MoneyXClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    def _auth_headers(self, include_mxi: bool = False) -> dict[str, str]:
        if not self.token:
            raise AuthenticationError("Money-X token is not configured")
        headers = {"Authorization": f"Bearer {self.token}"}
        if include_mxi and self.mxi_token:
            headers["Cookie"] = f"mxi_token={self.mxi_token}"
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: object | None = None,
        include_mxi: bool | None = None,
        attempts: int = 3,
    ) -> httpx.Response:
        url = f"{self.api_url}{path}"
        if include_mxi is None:
            include_mxi = self._use_mxi
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                response = await self._client.request(
                    method, url, json=json, headers=self._auth_headers(include_mxi)
                )
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = exc
                if attempt + 1 == attempts:
                    raise TransientAPIError("Money-X network request failed") from exc
            else:
                if response.status_code == 401:
                    raise AuthenticationError("Money-X session has expired")
                if response.status_code == 403:
                    raise ForbiddenError("Money-X rejected the request")
                if response.status_code not in _RETRYABLE:
                    try:
                        response.raise_for_status()
                    except httpx.HTTPStatusError as exc:
                        raise MoneyXError(f"Money-X returned HTTP {response.status_code}") from exc
                    return response
                last_error = TransientAPIError(
                    f"Money-X returned temporary HTTP {response.status_code}"
                )
                if attempt + 1 == attempts:
                    raise last_error
            delay = min(4.0, 0.35 * (2**attempt)) + random.uniform(0, 0.2)
            await asyncio.sleep(delay)
        raise TransientAPIError("Money-X request failed") from last_error

    @staticmethod
    def _json(response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError as exc:
            raise APIContractError("Money-X returned non-JSON response") from exc

    async def authenticate(self) -> AuthResult:
        for include_mxi in (False, True):
            if include_mxi and not self.mxi_token:
                continue
            try:
                response = await self._request("GET", "/v3/api/me", include_mxi=include_mxi)
                payload = self._json(response)
                user = payload.get("data", {}).get("user") if isinstance(payload, dict) else None
                if not isinstance(user, dict):
                    raise AuthenticationError("Money-X response contains no authenticated user")
                self._use_mxi = include_mxi
                return AuthResult(user=AuthUser.model_validate(user))
            except (AuthenticationError, ForbiddenError):
                if include_mxi or not self.mxi_token:
                    raise
        raise AuthenticationError("Money-X session has expired")

    async def list_crypto(self) -> list[CryptoPair]:
        response = await self._request("POST", "/v3/api/deposit/crypto/list")
        payload = self._json(response)
        items = _find_crypto_items(payload)
        if items is None:
            raise APIContractError("crypto/list schema has changed: list not found")
        pairs: list[CryptoPair] = []
        invalid = 0
        for item in items:
            try:
                pair = CryptoPair.model_validate(item)
                if not pair.currency.strip() or not pair.network.strip():
                    raise ValueError("empty currency or network")
                pairs.append(pair)
            except (ValidationError, ValueError):
                invalid += 1
        if not pairs:
            raise APIContractError("crypto/list has no valid currency/network entries")
        if invalid:
            raise APIContractError(f"crypto/list contains {invalid} invalid entries")
        return pairs

    async def wallet_rate(self, pair: CryptoPair) -> Any:
        response = await self._request(
            "POST",
            "/v3/api/deposit/crypto/wallet",
            json={"network": pair.network, "currency": pair.currency},
        )
        payload = self._json(response)
        raw = _find_rate(payload)
        if raw is None:
            raise APIContractError("crypto/wallet schema has changed: rate not found")
        try:
            return as_decimal(raw)
        except SchemaError as exc:
            raise APIContractError(str(exc)) from exc


def _find_crypto_items(value: Any) -> list[Mapping[str, Any]] | None:
    if isinstance(value, list):
        objects = [item for item in value if isinstance(item, Mapping)]
        if objects and any(
            any(key in item for key in ("currency", "code", "coin")) for item in objects
        ):
            return objects
        for item in value:
            found = _find_crypto_items(item)
            if found is not None:
                return found
    elif isinstance(value, Mapping):
        for key in ("items", "currencies", "crypto", "list", "data"):
            if key in value:
                found = _find_crypto_items(value[key])
                if found is not None:
                    return found
    return None


def _find_rate(value: Any) -> Any | None:
    if isinstance(value, Mapping):
        for key in ("rate", "course", "exchange_rate"):
            if value.get(key) is not None:
                return value[key]
        for key in ("data", "wallet", "result"):
            if key in value:
                found = _find_rate(value[key])
                if found is not None:
                    return found
    return None


async def discover_domain(
    raw_web_url: str,
    *,
    token: str | None,
    mxi_token: str | None,
    timeout: float = 12.0,
    allowed_api_hosts: set[str] | None = None,
) -> DomainPair:
    web_url = parse_web_url(raw_web_url)
    hostname = httpx.URL(web_url).host
    await assert_public_dns(hostname)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        current = web_url
        response: httpx.Response | None = None
        for _ in range(6):
            parsed_current = httpx.URL(current)
            if parsed_current.scheme != "https" or parsed_current.port not in (None, 443):
                raise DomainValidationError("небезопасный редирект")
            await assert_public_dns(parsed_current.host)
            response = await client.get(current, headers={"User-Agent": "MoneyX-Rate-Monitor/1.0"})
            if not response.is_redirect:
                break
            location = response.headers.get("location")
            if not location:
                raise DomainValidationError("редирект без Location")
            current = urljoin(current, location)
        else:
            raise DomainValidationError("слишком много редиректов")
        assert response is not None
        response.raise_for_status()
        configured = _CONFIG_API_RE.findall(response.text)
        matches = configured or _API_URL_RE.findall(response.text)
    api_url = matches[0].rstrip("/") if matches else derive_api_url(web_url)
    parsed_api = parse_web_url(api_url)
    api_host = httpx.URL(parsed_api).host.lower()
    expected_api_host = httpx.URL(derive_api_url(web_url)).host.lower()
    if api_host != expected_api_host and api_host not in (allowed_api_hosts or set()):
        raise DomainValidationError(
            "apiBase points to an unexpected host"
        )
    await assert_public_dns(api_host)
    async with MoneyXClient(
        web_url, parsed_api, token=token, mxi_token=mxi_token, timeout=timeout
    ) as moneyx:
        await moneyx.authenticate()
        await moneyx.list_crypto()
    return DomainPair(web_url=web_url, api_url=parsed_api)
