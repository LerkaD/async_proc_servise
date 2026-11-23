import asyncio
import hashlib
import hmac
import json
from http import HTTPStatus

import httpx

from src.application.validators import public_url
from src.application.validators.public_url import ensure_public_https_url
from src.domain.value_objects.webhook import WebhookNotification


def build_webhook_client(
    *,
    connect_timeout: float,
    read_timeout: float,
    max_connections: int,
) -> httpx.AsyncClient:
    timeout = httpx.Timeout(
        connect=connect_timeout,
        read=read_timeout,
        write=read_timeout,
        pool=connect_timeout,
    )
    limits = httpx.Limits(
        max_connections=max_connections,
        max_keepalive_connections=0,
        keepalive_expiry=0,
    )
    return httpx.AsyncClient(
        timeout=timeout,
        limits=limits,
        follow_redirects=False,
        transport=PublicWebhookTransport(limits=limits),
    )


class WebhookService:
    def __init__(self, client: httpx.AsyncClient, secret: str) -> None:
        self._client = client
        self._secret = secret.encode()

    async def send(self, url: str, notification: WebhookNotification) -> None:
        try:
            await asyncio.to_thread(ensure_public_https_url, url)
        except ValueError as exc:
            raise RuntimeError("webhook url is not public") from exc
        raw = json.dumps(
            {
                "payment_id": str(notification.payment_id),
                "status": notification.status,
                "amount": format(notification.amount, "f"),
                "currency": notification.currency,
                "processed_at": (
                    notification.processed_at.isoformat()
                    if notification.processed_at
                    else None
                ),
                "failure_reason": notification.failure_reason,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        signature = hmac.new(self._secret, raw, hashlib.sha256).hexdigest()
        try:
            response = await self._client.post(
                url,
                content=raw,
                follow_redirects=False,
                headers={
                    "Content-Type": "application/json",
                    "X-Signature": signature,
                    "X-Payment-Id": str(notification.payment_id),
                },
            )
        except httpx.TimeoutException as exc:
            raise RuntimeError("webhook timed out") from exc
        except httpx.HTTPError as exc:
            raise RuntimeError("webhook request failed") from exc

        if (
            response.status_code < int(HTTPStatus.OK)
            or response.status_code >= int(HTTPStatus.MULTIPLE_CHOICES)
        ):
            raise RuntimeError(f"webhook returned {response.status_code}")


class PublicWebhookTransport(httpx.AsyncHTTPTransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if not hasattr(request, "_content"):
            await request.aread()
        try:
            pinned = await asyncio.to_thread(pin_webhook_request, request)
        except ValueError as exc:
            raise httpx.ConnectError(str(exc)) from exc
        return await super().handle_async_request(pinned)


def pin_webhook_request(request: httpx.Request) -> httpx.Request:
    host = request.url.host
    port = request.url.port or (443 if request.url.scheme == "https" else 80)
    addresses = public_url.resolve_addresses(host, port)
    public_url.assert_all_global(addresses)
    headers = request.headers.copy()
    if (request.url.scheme == "https" and port == 443) or (
        request.url.scheme == "http" and port == 80
    ):
        headers["host"] = host
    else:
        headers["host"] = f"{host}:{port}"
    pinned = httpx.Request(
        request.method,
        request.url.copy_with(host=str(addresses[0])),
        headers=headers,
        content=request.content,
        extensions={**request.extensions, "sni_hostname": host},
    )
    return pinned
