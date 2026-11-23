from __future__ import annotations

import logging
import uuid
from datetime import timedelta

from src.application.dto.processing import Decision, Snapshot
from src.application.enums.processing import Lease, Step
from src.application.exceptions import (
    PaymentNotFound,
    ProcessingBusy,
    TransientProcessingError,
)
from src.application.lease import keep_lease
from src.domain.clock import utcnow
from src.domain.constants import DECLINED, ERROR_MESSAGE_MAX_LENGTH
from src.domain.enums.payment import PaymentStatus
from src.application.protocols.gateway import Gateway
from src.application.protocols.uow import UnitOfWorkFactory
from src.application.protocols.webhook import WebhookSender
from src.domain.retry import retry_delay_seconds
from src.domain.value_objects.gateway import GatewayChargeResult
from src.domain.value_objects.webhook import WebhookNotification

logger = logging.getLogger(__name__)


class PaymentProcessingService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        gateway: Gateway,
        webhooks: WebhookSender,
        *,
        lease_seconds: float,
        webhook_retry_base_seconds: float = 2.0,
        webhook_retry_max_seconds: float = 60.0,
    ) -> None:
        self._uow_factory = uow_factory
        self._gateway = gateway
        self._webhooks = webhooks
        self._lease_seconds = lease_seconds
        self._webhook_retry_base_seconds = webhook_retry_base_seconds
        self._webhook_retry_max_seconds = webhook_retry_max_seconds

    async def handle(self, payment_id: uuid.UUID, *, processing_token: str | None) -> None:
        decision = await self._claim(payment_id, processing_token)
        if decision.step is Step.DONE:
            return
        if decision.step is Step.WEBHOOK:
            await self._deliver(decision)
            return

        if await self._refresh_lease(decision.snapshot.id, decision.token) is not Lease.HELD:
            raise ProcessingBusy(
                "processing token was replaced",
                processing_token=decision.token,
            )
        try:
            async with keep_lease(
                decision.snapshot.id,
                lambda: self._refresh_lease(decision.snapshot.id, decision.token),
                lease_seconds=self._lease_seconds,
            ):
                result = await self._gateway.charge(
                    decision.snapshot.id,
                    decision.snapshot.amount,
                    decision.snapshot.currency,
                )
        except RuntimeError as exc:
            raise TransientProcessingError(
                str(exc),
                processing_token=decision.token,
            ) from exc

        snapshot = await self._apply_gateway_result(payment_id, decision.token, result)
        await self._deliver(Decision(step=Step.WEBHOOK, token=decision.token, snapshot=snapshot))

    async def mark_exhausted(self, payment_id: uuid.UUID, reason: str) -> None:
        decision = await self._finalize_exhausted(payment_id, reason)
        if decision is None:
            return
        try:
            await self._deliver(decision)
        except TransientProcessingError:
            await self._schedule_webhook(payment_id)

    async def deliver_due_webhooks(self, *, limit: int = 20) -> int:
        due = await self._lease_due_webhooks(limit)
        delivered = 0
        for snapshot in due:
            try:
                await self._webhooks.send(
                    snapshot.webhook_url,
                    WebhookNotification(
                        payment_id=snapshot.id,
                        status=snapshot.status,
                        amount=snapshot.amount,
                        currency=snapshot.currency,
                        processed_at=snapshot.processed_at,
                        failure_reason=snapshot.failure_reason,
                    ),
                )
            except RuntimeError:
                await self._schedule_webhook(snapshot.id)
                continue
            if await self._mark_webhook_delivered(snapshot):
                delivered += 1
        return delivered

    async def _claim(self, payment_id: uuid.UUID, processing_token: str | None) -> Decision:
        async with self._uow_factory() as uow:
            payment = await uow.payments.get_for_update(payment_id)
            if payment is None:
                raise PaymentNotFound("payment not found")

            snapshot = Snapshot.from_payment(payment)
            if payment.webhook_delivered_at is not None:
                return Decision(
                    step=Step.DONE,
                    token=payment.processing_token or "",
                    snapshot=snapshot,
                )

            now = utcnow()
            owns_token = (
                processing_token is not None and processing_token == payment.processing_token
            )
            terminal = payment.status in (PaymentStatus.SUCCEEDED, PaymentStatus.FAILED)
            started = payment.processing_started_at
            lease_active = (
                started is not None
                and (now - started).total_seconds() < self._lease_seconds
            )
            if (
                not owns_token
                and lease_active
                and (payment.status == PaymentStatus.PROCESSING or terminal)
            ):
                raise ProcessingBusy(
                    "payment is being processed",
                    processing_token=processing_token,
                )

            token = processing_token if owns_token and processing_token else str(uuid.uuid4())
            if not terminal:
                payment.status = PaymentStatus.PROCESSING
            payment.processing_token = token
            payment.processing_started_at = now
            payment.updated_at = now
            step = Step.WEBHOOK if terminal else Step.CHARGE
            return Decision(step=step, token=token, snapshot=Snapshot.from_payment(payment))

    async def _apply_gateway_result(
        self,
        payment_id: uuid.UUID,
        token: str,
        result: GatewayChargeResult,
    ) -> Snapshot:
        async with self._uow_factory() as uow:
            payment = await uow.payments.get_for_update(payment_id)
            if payment is None:
                raise PaymentNotFound("payment not found")
            if (payment.status == PaymentStatus.SUCCEEDED and payment.provider_reference) or (
                payment.status == PaymentStatus.FAILED
                and payment.failure_reason is not None
                and payment.processed_at is not None
            ):
                return Snapshot.from_payment(payment)
            if payment.processing_token != token:
                raise ProcessingBusy(
                    "processing token was replaced",
                    processing_token=token,
                )

            now = utcnow()
            if result.ok:
                payment.status = PaymentStatus.SUCCEEDED
                payment.failure_reason = None
                payment.provider_reference = result.provider_reference
            else:
                payment.status = PaymentStatus.FAILED
                payment.failure_reason = (result.error_code or DECLINED)[
                    :ERROR_MESSAGE_MAX_LENGTH
                ]
                payment.provider_reference = None
            payment.webhook_delivered_at = None
            payment.processed_at = now
            payment.updated_at = now
            return Snapshot.from_payment(payment)

    async def _deliver(self, decision: Decision) -> None:
        snapshot = await self._webhook_target(decision)
        if snapshot is None:
            return
        if await self._refresh_lease(snapshot.id, decision.token) is not Lease.HELD:
            raise ProcessingBusy(
                "processing token was replaced",
                processing_token=decision.token,
            )
        async with keep_lease(
            snapshot.id,
            lambda: self._refresh_lease(snapshot.id, decision.token),
            lease_seconds=self._lease_seconds,
        ):
            try:
                await self._webhooks.send(
                    snapshot.webhook_url,
                    WebhookNotification(
                        payment_id=snapshot.id,
                        status=snapshot.status,
                        amount=snapshot.amount,
                        currency=snapshot.currency,
                        processed_at=snapshot.processed_at,
                        failure_reason=snapshot.failure_reason,
                    ),
                )
            except RuntimeError as exc:
                raise TransientProcessingError(
                    str(exc),
                    processing_token=decision.token,
                ) from exc
        await self._mark_webhook_delivered(snapshot)

    async def _finalize_exhausted(
        self,
        payment_id: uuid.UUID,
        reason: str,
    ) -> Decision | None:
        async with self._uow_factory() as uow:
            payment = await uow.payments.get_for_update(payment_id)
            if payment is None or payment.webhook_delivered_at is not None:
                return None
            now = utcnow()
            due = payment.webhook_next_retry_at
            if due is not None and due > now:
                return None
            if payment.status in (PaymentStatus.PENDING, PaymentStatus.PROCESSING):
                payment.status = PaymentStatus.FAILED
                payment.failure_reason = reason[:ERROR_MESSAGE_MAX_LENGTH]
                payment.processed_at = now
                payment.updated_at = now
            if payment.status not in (PaymentStatus.SUCCEEDED, PaymentStatus.FAILED):
                return None
            token = payment.processing_token or str(uuid.uuid4())
            payment.processing_token = token
            payment.processing_started_at = now
            payment.updated_at = now
            return Decision(step=Step.WEBHOOK, token=token, snapshot=Snapshot.from_payment(payment))

    async def _webhook_target(self, decision: Decision) -> Snapshot | None:
        async with self._uow_factory() as uow:
            payment = await uow.payments.get_for_update(decision.snapshot.id)
            if payment is None or payment.webhook_delivered_at is not None:
                return None
            if payment.processing_token != decision.token:
                raise ProcessingBusy(
                    "processing token was replaced",
                    processing_token=decision.token,
                )
            return Snapshot.from_payment(payment)

    async def _lease_due_webhooks(self, limit: int) -> list[Snapshot]:
        until = utcnow() + timedelta(seconds=self._lease_seconds)
        async with self._uow_factory() as uow:
            payments = await uow.payments.claim_due_webhooks(
                limit=limit,
                now=utcnow(),
            )
            snapshots: list[Snapshot] = []
            for payment in payments:
                payment.webhook_next_retry_at = until
                payment.updated_at = utcnow()
                snapshots.append(Snapshot.from_payment(payment))
            return snapshots

    async def _mark_webhook_delivered(self, snapshot: Snapshot) -> bool:
        async with self._uow_factory() as uow:
            payment = await uow.payments.get_for_update(snapshot.id)
            if payment is None:
                return False
            if payment.status.value != snapshot.status:
                return False
            if payment.webhook_delivered_at is not None:
                return False
            now = utcnow()
            payment.webhook_delivered_at = now
            payment.webhook_next_retry_at = None
            payment.updated_at = now
            return True

    async def _schedule_webhook(self, payment_id: uuid.UUID) -> None:
        scheduled: float | None = None
        async with self._uow_factory() as uow:
            payment = await uow.payments.get_for_update(payment_id)
            if payment is None or payment.webhook_delivered_at is not None:
                return
            if payment.status not in (PaymentStatus.SUCCEEDED, PaymentStatus.FAILED):
                return
            attempts = payment.webhook_attempts + 1
            payment.webhook_attempts = attempts
            scheduled = retry_delay_seconds(
                attempts,
                base=self._webhook_retry_base_seconds,
                cap=self._webhook_retry_max_seconds,
            )
            payment.webhook_next_retry_at = utcnow() + timedelta(seconds=scheduled)
            payment.updated_at = utcnow()
        if scheduled is None:
            return
        logger.warning(
            "webhook for payment %s was not delivered, next try in %.1fs",
            payment_id,
            scheduled,
        )

    async def _refresh_lease(self, payment_id: uuid.UUID, token: str) -> Lease:
        async with self._uow_factory() as uow:
            payment = await uow.payments.get_for_update(payment_id)
            if payment is None:
                return Lease.LOST
            if payment.webhook_delivered_at is not None:
                return Lease.DONE
            if payment.processing_token != token:
                return Lease.LOST
            if payment.status not in (
                PaymentStatus.PROCESSING,
                PaymentStatus.SUCCEEDED,
                PaymentStatus.FAILED,
            ):
                return Lease.LOST
            payment.processing_started_at = utcnow()
            return Lease.HELD
