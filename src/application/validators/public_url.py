import ipaddress
import socket
from collections.abc import Sequence

import httpx

from src.application.validators.constants import LOCAL_NAMES

Address = ipaddress.IPv4Address | ipaddress.IPv6Address


def resolve_addresses(host: str, port: int) -> list[Address]:
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        return [literal]

    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError("webhook_url host did not resolve") from exc

    addresses: list[Address] = []
    seen: set[str] = set()
    for *_, sockaddr in infos:
        text = str(sockaddr[0]).split("%", 1)[0]
        address = ipaddress.ip_address(text)
        key = address.compressed
        if key in seen:
            continue
        seen.add(key)
        addresses.append(address)
    if not addresses:
        raise ValueError("webhook_url host did not resolve")
    return addresses


def ensure_public_https_url(url: str) -> None:
    parsed = httpx.URL(url)
    if parsed.scheme != "https":
        raise ValueError("webhook_url must use https")
    if parsed.username or parsed.password:
        raise ValueError("webhook_url must not include credentials")
    host = (parsed.host or "").rstrip(".").lower()
    if (
        host == ""
        or host in LOCAL_NAMES
        or host.endswith(".localhost")
        or host.endswith(".local")
    ):
        raise ValueError("webhook_url must not target a local host")
    port = parsed.port or 443
    addresses = resolve_addresses(host, port)
    if any(not address.is_global for address in addresses):
        raise ValueError("webhook_url must not target a non-public address")


def assert_all_global(addresses: Sequence[Address]) -> None:
    if not addresses or any(not address.is_global for address in addresses):
        raise ValueError("webhook_url must not target a non-public address")
