from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.clock import utcnow
from src.domain.entities.idempotency import IdempotencyKey
from src.infrastructure.db.mappers.idempotency import apply_idempotency, idempotency_to_entity
from src.infrastructure.db.models.idempotency import IdempotencyKeyModel


class SqlaIdempotencyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._tracked: dict[str, tuple[IdempotencyKeyModel, IdempotencyKey]] = {}

    async def insert_if_absent(self, *, key: str, request_hash: str) -> bool:
        statement = (
            pg_insert(IdempotencyKeyModel)
            .values(key=key, request_hash=request_hash, created_at=utcnow())
            .on_conflict_do_nothing(index_elements=["key"])
            .returning(IdempotencyKeyModel.key)
        )
        result = await self._session.execute(statement)
        return result.scalar_one_or_none() is not None

    async def get(self, key: str) -> IdempotencyKey | None:
        model = await self._session.get(IdempotencyKeyModel, key)
        if model is None:
            return None
        return self._track(model)

    async def get_for_update(self, key: str) -> IdempotencyKey | None:
        statement = (
            select(IdempotencyKeyModel)
            .where(IdempotencyKeyModel.key == key)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        result = await self._session.execute(statement)
        model = result.scalar_one_or_none()
        if model is None:
            return None
        return self._track(model)

    def sync_all(self) -> None:
        for model, entity in self._tracked.values():
            apply_idempotency(entity, model)

    def _track(self, model: IdempotencyKeyModel) -> IdempotencyKey:
        entity = idempotency_to_entity(model)
        self._tracked[entity.key] = (model, entity)
        return entity
