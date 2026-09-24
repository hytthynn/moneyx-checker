from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx


class DomainValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DomainPair:
    web_url: str
    api_url: str


def _is_public_ip(raw: str) -> bool:
    ip = ipaddress.ip_address(raw)
    return not any(
        (
            ip.is_private,
            ip.is_loopback,
            ip.is_link_local,
            ip.is_multicast,
            ip.is_reserved,
            ip.is_unspecified,
        )
    )


def parse_web_url(raw: str) -> str:
    candidate = raw.strip()
    if "://" not in candidate:
        candidate = "https://" + candidate
    parsed = urlparse(candidate)
    if parsed.scheme != "https":
        raise DomainValidationError("разрешён только HTTPS")
    if not parsed.hostname or parsed.username or parsed.password:
        raise DomainValidationError("некорректный hostname или userinfo")
    if parsed.port not in (None, 443):
        raise DomainValidationError("нестандартный порт запрещён")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise DomainValidationError("URL не должен содержать путь, query или fragment")
    host = parsed.hostname.rstrip(".").lower()
    if host == "localhost" or host.endswith(".localhost"):
        raise DomainValidationError("localhost запрещён")
    try:
        parsed_ip = ipaddress.ip_address(host)
    except ValueError:
        parsed_ip = None
    if parsed_ip is not None:
        raise DomainValidationError("IP-адреса запрещены; укажите hostname")
    if "." not in host:
        raise DomainValidationError("hostname должен быть полным доменным именем")
    return f"https://{host}"


def derive_api_url(web_url: str) -> str:
    host = urlparse(parse_web_url(web_url)).hostname
    assert host
    if host.startswith("api."):
        return f"https://{host}"
    return f"https://api.{host}"


async def assert_public_dns(hostname: str) -> None:
    loop = asyncio.get_running_loop()
    try:
        results = await loop.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise DomainValidationError("домен не разрешается через DNS") from exc
    addresses = {item[4][0] for item in results}
    if not addresses or any(not _is_public_ip(address) for address in addresses):
        raise DomainValidationError("домен разрешается в непубличный IP-адрес")


async def validate_redirect_chain(response: httpx.Response) -> None:
    for item in [*response.history, response]:
        host = item.url.host
        if not host:
            raise DomainValidationError("редирект без hostname")
        await assert_public_dns(host)
        if item.url.scheme != "https" or item.url.port not in (None, 443):
            raise DomainValidationError("небезопасный редирект")
