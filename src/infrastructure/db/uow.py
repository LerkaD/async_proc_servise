from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.application.protocols.repositories import (
    IdempotencyRepository,
    OutboxRepository,
    PaymentRepository,
)
from src.application.protocols.uow import UnitOfWork
from src.infrastructure.db.repositories.idempotency import SqlaIdempotencyRepository
from src.infrastructure.db.repositories.outbox import SqlaOutboxRepository
from src.infrastructure.db.repositories.payment import SqlaPaymentRepository


class SqlaUnitOfWork:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._session_cm: Any = None
        self._session: AsyncSession | None = None
        self._transaction: Any = None
        self._payments_impl: SqlaPaymentRepository | None = None
        self._idempotency_impl: SqlaIdempotencyRepository | None = None
        self._outbox_impl: SqlaOutboxRepository | None = None
        self.payments: PaymentRepository
        self.idempotency: IdempotencyRepository
        self.outbox: OutboxRepository

    async def __aenter__(self) -> SqlaUnitOfWork:
        self._session_cm = self._session_factory()
        self._session = await self._session_cm.__aenter__()
        self._transaction = self._session.begin()
        await self._transaction.__aenter__()
        self._payments_impl = SqlaPaymentRepository(self._session)
        self._idempotency_impl = SqlaIdempotencyRepository(self._session)
        self._outbox_impl = SqlaOutboxRepository(self._session)
        self.payments = self._payments_impl
        self.idempotency = self._idempotency_impl
        self.outbox = self._outbox_impl
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object,
    ) -> None:
        if exc_type is None:
            assert self._payments_impl is not None
            assert self._idempotency_impl is not None
            assert self._outbox_impl is not None
            self._payments_impl.sync_all()
            self._idempotency_impl.sync_all()
            self._outbox_impl.sync_all()
        assert self._transaction is not None
        assert self._session_cm is not None
        await self._transaction.__aexit__(exc_type, exc, tb)
        await self._session_cm.__aexit__(exc_type, exc, tb)


class SqlaUnitOfWorkFactory:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    def __call__(self) -> UnitOfWork:
        return SqlaUnitOfWork(self._session_factory)
