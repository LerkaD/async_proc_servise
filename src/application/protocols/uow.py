from __future__ import annotations

from typing import Protocol, Self

from src.application.protocols.repositories import (
    IdempotencyRepository,
    OutboxRepository,
    PaymentRepository,
)


class UnitOfWork(Protocol):
    payments: PaymentRepository
    idempotency: IdempotencyRepository
    outbox: OutboxRepository

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object,
    ) -> None: ...


class UnitOfWorkFactory(Protocol):
    def __call__(self) -> UnitOfWork: ...
