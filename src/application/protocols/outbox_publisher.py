from __future__ import annotations

from typing import Protocol

from src.domain.value_objects.outbox_message import OutboxMessage


class OutboxPublisher(Protocol):
    async def publish(self, event: OutboxMessage) -> None: ...
