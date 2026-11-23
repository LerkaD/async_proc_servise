from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.application.services.outbox import OutboxRelayService
from src.domain.clock import utcnow
from src.domain.entities.outbox import OutboxEvent
from src.domain.enums.outbox import OutboxStatus
from src.infrastructure.db.models.outbox import OutboxEventModel
from src.infrastructure.db.repositories.outbox import SqlaOutboxRepository
from src.infrastructure.db.uow import SqlaUnitOfWorkFactory

SessionFactory = async_sessionmaker[AsyncSession]
InsertPayment = Callable[[AsyncSession], Awaitable[uuid.UUID]]


@pytest.mark.commit
async def test_claim_skips_locked_rows(
    session_factory: SessionFactory,
    insert_payment: InsertPayment,
) -> None:
    async with session_factory() as session, session.begin():
        now = utcnow()
        repo = SqlaOutboxRepository(session)
        for _ in range(4):
            await repo.add(
                OutboxEvent(
                    id=uuid.uuid4(),
                    aggregate_id=await insert_payment(session),
                    event_type="payment.created",
                    payload={"payment_id": str(uuid.uuid4())},
                    status=OutboxStatus.PENDING,
                    retry_count=0,
                    next_retry_at=None,
                    last_error=None,
                    created_at=now,
                    published_at=None,
                ),
            )

    first_locked = asyncio.Event()
    release = asyncio.Event()

    async def claim_first() -> list[uuid.UUID]:
        async with session_factory() as session, session.begin():
            rows = await SqlaOutboxRepository(session).claim_pending(limit=2, now=utcnow())
            ids = [row.id for row in rows]
            first_locked.set()
            await release.wait()
            return ids

    async def claim_second() -> list[uuid.UUID]:
        await first_locked.wait()
        async with session_factory() as session, session.begin():
            rows = await SqlaOutboxRepository(session).claim_pending(limit=2, now=utcnow())
            return [row.id for row in rows]

    first_task = asyncio.create_task(claim_first())
    try:
        second_ids = await asyncio.wait_for(claim_second(), timeout=5)
    finally:
        release.set()
    first_ids = await first_task

    assert len(first_ids) == 2
    assert len(second_ids) == 2
    assert set(first_ids).isdisjoint(second_ids)


async def test_publish_stops_after_max_retries(
    session_factory: SessionFactory,
    insert_payment: InsertPayment,
) -> None:
    async with session_factory() as session, session.begin():
        now = utcnow()
        payment_id = await insert_payment(session)
        await SqlaOutboxRepository(session).add(_pending(payment_id, now))

    class _Down:
        def __init__(self) -> None:
            self.calls = 0

        async def publish(self, event: object) -> None:
            del event
            self.calls += 1
            raise RuntimeError("broker down")

    publisher = _Down()
    service = OutboxRelayService(SqlaUnitOfWorkFactory(session_factory))
    for _ in range(4):
        await service.publish_pending(
            publisher,
            batch_size=10,
            base_delay=0,
            max_delay=0,
            max_retries=3,
            lease_seconds=5,
        )

    assert publisher.calls == 3
    async with session_factory() as session, session.begin():
        event = (await session.execute(select(OutboxEventModel))).scalar_one()
        assert event.status == OutboxStatus.FAILED
        assert event.retry_count == 3
        assert event.next_retry_at is not None


@pytest.mark.commit
async def test_publish_does_not_keep_a_connection(
    session_factory: SessionFactory,
    insert_payment: InsertPayment,
    checked_out: Callable[[], int],
) -> None:
    async with session_factory() as session, session.begin():
        now = utcnow()
        payment_id = await insert_payment(session)
        await SqlaOutboxRepository(session).add(_pending(payment_id, now))

    seen: dict[str, int] = {}

    class _Publisher:
        async def publish(self, event: object) -> None:
            del event
            seen["checked_out"] = checked_out()

    published = await OutboxRelayService(SqlaUnitOfWorkFactory(session_factory)).publish_pending(
        _Publisher(),
        batch_size=10,
        base_delay=0,
        max_delay=0,
        max_retries=3,
        lease_seconds=5,
    )

    assert published == 1
    assert seen["checked_out"] == 0


async def test_failed_event_is_revived_when_due(
    session_factory: SessionFactory,
    insert_payment: InsertPayment,
) -> None:
    now = utcnow()
    async with session_factory() as session, session.begin():
        payment_id = await insert_payment(session)
        repo = SqlaOutboxRepository(session)
        await repo.add(_failed(payment_id, now, due=now - timedelta(seconds=1)))
        await repo.add(_failed(payment_id, now, due=now + timedelta(hours=1)))

    service = OutboxRelayService(SqlaUnitOfWorkFactory(session_factory))
    assert await service.revive_failed(limit=10) == 1

    class _Up:
        def __init__(self) -> None:
            self.calls = 0

        async def publish(self, event: object) -> None:
            del event
            self.calls += 1

    publisher = _Up()
    published = await service.publish_pending(
        publisher,
        batch_size=10,
        base_delay=1,
        max_delay=1,
        max_retries=3,
        lease_seconds=5,
    )

    assert publisher.calls == 1
    assert published == 1
    async with session_factory() as session, session.begin():
        rows = (await session.execute(select(OutboxEventModel))).scalars().all()
        assert sorted(row.status for row in rows) == [OutboxStatus.FAILED, OutboxStatus.PUBLISHED]


def _pending(payment_id: uuid.UUID, now: datetime) -> OutboxEvent:
    return OutboxEvent(
        id=uuid.uuid4(),
        aggregate_id=payment_id,
        event_type="payment.created",
        payload={"payment_id": str(payment_id)},
        status=OutboxStatus.PENDING,
        retry_count=0,
        next_retry_at=None,
        last_error=None,
        created_at=now,
        published_at=None,
    )


def _failed(payment_id: uuid.UUID, now: datetime, *, due: datetime) -> OutboxEvent:
    return OutboxEvent(
        id=uuid.uuid4(),
        aggregate_id=payment_id,
        event_type="payment.created",
        payload={"payment_id": str(payment_id)},
        status=OutboxStatus.FAILED,
        retry_count=3,
        next_retry_at=due,
        last_error="broker down",
        created_at=now,
        published_at=None,
    )
