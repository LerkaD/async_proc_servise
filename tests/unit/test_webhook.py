from __future__ import annotations

import hashlib
import hmac
import ipaddress
from collections.abc import Callable

import httpx
import pytest
from fastapi import status
from pydantic import ValidationError

from src.application.schemas.payment import PaymentCreateSchema
from src.application.validators.public_url import ensure_public_https_url
from src.domain.value_objects.webhook import WebhookNotification
from src.infrastructure.clients.webhook import (
    WebhookService,
    build_webhook_client,
    pin_webhook_request,
)

pytestmark = pytest.mark.nodb


def test_url_rejects_plaintext_and_local_targets(
    payload: Callable[..., dict[str, object]],
) -> None:
    with pytest.raises(ValidationError):
        PaymentCreateSchema.model_validate(payload(webhook_url="http://example.com/hook"))
    with pytest.raises(ValidationError):
        PaymentCreateSchema.model_validate(payload(webhook_url="https://127.0.0.1/hook"))
    with pytest.raises(ValidationError):
        PaymentCreateSchema.model_validate(payload(webhook_url="https://localhost/hook"))


def test_url_rejects_a_name_that_resolves_privately(
    monkeypatch: pytest.MonkeyPatch,
    payload: Callable[..., dict[str, object]],
) -> None:
    def resolve_to(address: str):
        def _resolve(host: str, port: int) -> list[ipaddress.IPv4Address]:
            del host, port
            return [ipaddress.ip_address(address)]

        return _resolve

    resolve_path = "src.application.validators.public_url.resolve_addresses"
    for address in ("10.1.2.3", "169.254.169.254"):
        monkeypatch.setattr(resolve_path, resolve_to(address))
        with pytest.raises(ValidationError):
            PaymentCreateSchema.model_validate(payload(webhook_url="https://merchant.example/hook"))

    def mixed(host: str, port: int) -> list[ipaddress.IPv4Address]:
        del host, port
        return [ipaddress.ip_address("93.184.216.34"), ipaddress.ip_address("10.0.0.1")]

    monkeypatch.setattr(resolve_path, mixed)
    with pytest.raises(ValueError, match="non-public"):
        ensure_public_https_url("https://merchant.example/hook")


def test_request_is_pinned_to_a_public_address() -> None:
    request = httpx.Request("POST", "https://merchant.example/hook", content=b"{}")
    pinned = pin_webhook_request(request)

    assert pinned.url.host == "93.184.216.34"
    assert pinned.headers["host"] == "merchant.example"
    assert pinned.extensions["sni_hostname"] == "merchant.example"
    assert pinned.content == b"{}"


async def test_body_is_signed_and_non_2xx_is_rejected(
    sample_notification: WebhookNotification,
) -> None:
    seen: dict[str, bytes | str] = {}

    def ok(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.content
        seen["signature"] = request.headers["X-Signature"]
        return httpx.Response(status.HTTP_204_NO_CONTENT)

    client = httpx.AsyncClient(transport=httpx.MockTransport(ok))
    await WebhookService(client, "test-webhook-secret").send(
        "https://merchant.example/hook",
        sample_notification,
    )
    await client.aclose()

    body = seen["body"]
    assert isinstance(body, bytes)
    expected = hmac.new(b"test-webhook-secret", body, hashlib.sha256).hexdigest()
    assert seen["signature"] == expected

    def failed(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status.HTTP_502_BAD_GATEWAY)

    failing = httpx.AsyncClient(transport=httpx.MockTransport(failed))
    with pytest.raises(RuntimeError, match="502"):
        await WebhookService(failing, "test-webhook-secret").send(
            "https://merchant.example/hook",
            sample_notification,
        )
    await failing.aclose()


async def test_client_does_not_follow_redirects(
    sample_notification: WebhookNotification,
) -> None:
    client = build_webhook_client(connect_timeout=1, read_timeout=1, max_connections=1)
    assert client.follow_redirects is False

    def redirect(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status.HTTP_302_FOUND,
            headers={"location": "https://127.0.0.1/hook"},
        )

    redirecting = httpx.AsyncClient(
        transport=httpx.MockTransport(redirect),
        follow_redirects=False,
    )
    with pytest.raises(
        RuntimeError,
        match=str(status.HTTP_302_FOUND),
    ):
        await WebhookService(redirecting, "test-webhook-secret").send(
            "https://merchant.example/hook",
            sample_notification,
        )
    await redirecting.aclose()
    await client.aclose()
