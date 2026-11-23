from __future__ import annotations

from typing import Protocol

from src.domain.value_objects.webhook import WebhookNotification


class WebhookSender(Protocol):
    async def send(self, url: str, notification: WebhookNotification) -> None: ...
