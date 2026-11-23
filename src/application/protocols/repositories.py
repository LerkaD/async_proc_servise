from __future__ import annotations

import uuid
from datetime import datetime
from typing import Protocol

from src.domain.entities.idempotency import IdempotencyKey
from src.domain.entities.outbox import OutboxEvent
from src.domain.entities.payment import Payment


class PaymentRepository(Protocol):
    async def add(self, payment: Payment) -> Payment: ...

    async def get(self, payment_id: uuid.UUID) -> Payment | None: ...

    async def get_for_update(self, payment_id: uuid.UUID) -> Payment | None: ...

    async def claim_due_webhooks(self, *, limit: int, now: datetime) -> list[Payment]: ...


class OutboxRepository(Protocol):
    async def add(self, event: OutboxEvent) -> OutboxEvent: ...

    async def get_for_update(self, event_id: uuid.UUID) -> OutboxEvent | None: ...

    async def claim_pending(self, *, limit: int, now: datetime) -> list[OutboxEvent]: ...

    async def claim_failed(self, *, limit: int, now: datetime) -> list[OutboxEvent]: ...


class IdempotencyRepository(Protocol):
    async def insert_if_absent(self, *, key: str, request_hash: str) -> bool: ...

    async def get(self, key: str) -> IdempotencyKey | None: ...

    async def get_for_update(self, key: str) -> IdempotencyKey | None: ...
