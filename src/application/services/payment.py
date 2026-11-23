from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from http import HTTPStatus
from typing import Any

from src.application.dto.payment import PaymentAccepted, PaymentDetails
from src.application.exceptions import (
    IdempotencyInProgress,
    IdempotencyPayloadMismatch,
    PaymentNotFound,
)
from src.application.schemas.payment import PaymentCreateSchema
from src.domain.clock import utcnow
from src.domain.constants import PAYMENT_CREATED_EVENT
from src.domain.entities.outbox import OutboxEvent
from src.domain.entities.payment import Payment
from src.domain.enums.currency import Currency
from src.domain.enums.outbox import OutboxStatus
from src.domain.enums.payment import PaymentStatus
from src.application.protocols.uow import UnitOfWorkFactory


class PaymentService:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def create_payment(
        self,
        payload: PaymentCreateSchema,
        idempotency_key: str,
    ) -> tuple[dict[str, Any], int]:
        normalized, digest = self._idempotency_payload(payload)

        async with self._uow_factory() as uow:
            return await self._create_or_replay(
                uow,
                payload=payload,
                normalized=normalized,
                digest=digest,
                idempotency_key=idempotency_key,
            )

    async def get_payment(self, payment_id: uuid.UUID) -> PaymentDetails:
        async with self._uow_factory() as uow:
            payment = await uow.payments.get(payment_id)
            if payment is None:
                raise PaymentNotFound("payment not found")
            return PaymentDetails(
                payment_id=payment.id,
                amount=payment.amount,
                currency=Currency(payment.currency),
                description=payment.description,
                metadata=payment.payment_metadata,
                status=payment.status,
                idempotency_key=payment.idempotency_key,
                webhook_url=payment.webhook_url,
                failure_reason=payment.failure_reason,
                provider_reference=payment.provider_reference,
                created_at=payment.created_at,
                processed_at=payment.processed_at,
                webhook_delivered_at=payment.webhook_delivered_at,
            )

    async def _create_or_replay(
        self,
        uow: Any,
        *,
        payload: PaymentCreateSchema,
        normalized: dict[str, Any],
        digest: str,
        idempotency_key: str,
    ) -> tuple[dict[str, Any], int]:
        inserted = await uow.idempotency.insert_if_absent(
            key=idempotency_key,
            request_hash=digest,
        )
        if not inserted:
            return await self._replay(uow, idempotency_key, digest)

        now = utcnow()
        payment = Payment(
            id=uuid.uuid4(),
            amount=payload.amount,
            currency=payload.currency.value,
            description=payload.description,
            payment_metadata=normalized["metadata"],
            status=PaymentStatus.PENDING,
            idempotency_key=idempotency_key,
            webhook_url=normalized["webhook_url"],
            failure_reason=None,
            provider_reference=None,
            processing_token=None,
            processing_started_at=None,
            webhook_delivered_at=None,
            webhook_attempts=0,
            webhook_next_retry_at=None,
            processed_at=None,
            created_at=now,
            updated_at=now,
        )
        await uow.payments.add(payment)

        accepted = PaymentAccepted(
            payment_id=payment.id,
            status=payment.status,
            created_at=payment.created_at,
        )
        created_at = accepted.created_at.isoformat()
        if created_at.endswith("+00:00"):
            created_at = f"{created_at.removesuffix('+00:00')}Z"
        body = {
            "payment_id": str(accepted.payment_id),
            "status": accepted.status.value,
            "created_at": created_at,
        }

        event = OutboxEvent(
            id=uuid.uuid4(),
            aggregate_id=payment.id,
            event_type=PAYMENT_CREATED_EVENT,
            payload={"payment_id": str(payment.id)},
            status=OutboxStatus.PENDING,
            retry_count=0,
            next_retry_at=None,
            last_error=None,
            created_at=now,
            published_at=None,
        )
        await uow.outbox.add(event)

        record = await uow.idempotency.get(idempotency_key)
        if record is None:
            raise IdempotencyInProgress("idempotency key was not stored")
        record.payment_id = payment.id
        record.status_code = int(HTTPStatus.ACCEPTED)
        record.response_body = body
        return body, int(HTTPStatus.ACCEPTED)

    async def _replay(
        self,
        uow: Any,
        idempotency_key: str,
        digest: str,
    ) -> tuple[dict[str, Any], int]:
        existing = await uow.idempotency.get_for_update(idempotency_key)
        if existing is None:
            raise IdempotencyInProgress(
                "request with this idempotency key is still in progress",
            )
        if not hmac.compare_digest(existing.request_hash, digest):
            raise IdempotencyPayloadMismatch(
                "idempotency key reused with a different payload",
            )
        if existing.response_body is None or existing.status_code is None:
            raise IdempotencyInProgress(
                "request with this idempotency key is still in progress",
            )
        return dict(existing.response_body), existing.status_code

    @staticmethod
    def _idempotency_payload(payload: PaymentCreateSchema) -> tuple[dict[str, Any], str]:
        dumped = payload.model_dump(mode="json")
        metadata = dumped["metadata"]
        webhook_url = dumped["webhook_url"]
        if not isinstance(metadata, dict) or not isinstance(webhook_url, str):
            raise ValueError("invalid payment payload")
        normalized = {
            "amount": format(payload.amount, "f"),
            "currency": payload.currency.value,
            "description": payload.description,
            "metadata": metadata,
            "webhook_url": webhook_url,
        }
        raw = json.dumps(
            normalized,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return normalized, hashlib.sha256(raw.encode()).hexdigest()
