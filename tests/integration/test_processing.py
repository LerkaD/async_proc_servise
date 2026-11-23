from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from decimal import Decimal

import pytest

from src.application.exceptions import ProcessingBusy, TransientProcessingError
from src.application.services.processing import PaymentProcessingService
from src.domain.constants import DECLINED
from src.domain.enums.payment import PaymentStatus
from src.domain.value_objects.gateway import GatewayChargeResult
from tests.support import FakeGateway, FakeWebhook

CreatePayment = Callable[[], Awaitable[uuid.UUID]]
ProcessingFactory = Callable[..., PaymentProcessingService]
PaymentState = Callable[[uuid.UUID], Awaitable[tuple[str, object, str | None]]]


async def test_charge_happens_once_when_the_webhook_is_retried(
    create_payment: CreatePayment,
    processing: ProcessingFactory,
    payment_state: PaymentState,
) -> None:
    payment_id = await create_payment()
    gateway = FakeGateway()
    webhooks = FakeWebhook(fail_times=1)
    service = processing(gateway, webhooks)

    with pytest.raises(TransientProcessingError) as caught:
        await service.handle(payment_id, processing_token=None)
    await service.handle(payment_id, processing_token=caught.value.processing_token)

    status, delivered, reason = await payment_state(payment_id)
    assert gateway.calls == 1
    assert webhooks.calls == 2
    assert status == PaymentStatus.SUCCEEDED
    assert delivered is not None
    assert reason is None


async def test_second_delivery_does_not_charge_again(
    create_payment: CreatePayment,
    processing: ProcessingFactory,
) -> None:
    payment_id = await create_payment()
    gateway = FakeGateway()
    webhooks = FakeWebhook()
    service = processing(gateway, webhooks)

    await service.handle(payment_id, processing_token=None)
    await service.handle(payment_id, processing_token=None)

    assert gateway.calls == 1
    assert webhooks.calls == 1


async def test_gateway_timeout_keeps_processing_until_retry(
    create_payment: CreatePayment,
    processing: ProcessingFactory,
    payment_state: PaymentState,
) -> None:
    payment_id = await create_payment()
    gateway = FakeGateway(errors=[RuntimeError("payment gateway timed out")])
    webhooks = FakeWebhook()
    service = processing(gateway, webhooks)

    with pytest.raises(TransientProcessingError) as caught:
        await service.handle(payment_id, processing_token=None)

    status, _, _ = await payment_state(payment_id)
    assert status == PaymentStatus.PROCESSING

    await service.handle(payment_id, processing_token=caught.value.processing_token)

    status, delivered, _ = await payment_state(payment_id)
    assert gateway.calls == 2
    assert webhooks.calls == 1
    assert status == PaymentStatus.SUCCEEDED
    assert delivered is not None


async def test_gateway_decline_is_terminal_and_notified(
    create_payment: CreatePayment,
    processing: ProcessingFactory,
    payment_state: PaymentState,
) -> None:
    payment_id = await create_payment()
    gateway = FakeGateway(
        result=GatewayChargeResult(ok=False, error_code="declined", provider_reference=None),
    )
    webhooks = FakeWebhook()

    await processing(gateway, webhooks).handle(
        payment_id,
        processing_token=None,
    )

    status, delivered, reason = await payment_state(payment_id)
    assert gateway.calls == 1
    assert webhooks.calls == 1
    assert webhooks.notifications[0].status == "failed"
    assert status == PaymentStatus.FAILED
    assert reason == "declined"
    assert delivered is not None


async def test_settled_decline_is_not_replaced_by_a_later_success(
    create_payment: CreatePayment,
    processing: ProcessingFactory,
    payment_state: PaymentState,
) -> None:
    payment_id = await create_payment()
    service = processing(
        FakeGateway(
            result=GatewayChargeResult(ok=False, error_code=DECLINED, provider_reference=None),
        ),
        FakeWebhook(fail_times=1),
    )
    with pytest.raises(TransientProcessingError) as caught:
        await service.handle(payment_id, processing_token=None)

    await service._apply_gateway_result(
        payment_id,
        caught.value.processing_token,
        GatewayChargeResult(ok=True, error_code=None, provider_reference="gw_late"),
    )

    status, delivered, reason = await payment_state(payment_id)
    assert status == PaymentStatus.FAILED
    assert reason == DECLINED
    assert delivered is None


async def test_exhausted_attempt_fails_only_an_unfinished_payment(
    create_payment: CreatePayment,
    processing: ProcessingFactory,
    payment_state: PaymentState,
) -> None:
    unfinished = await create_payment()
    gateway = FakeGateway(errors=[RuntimeError("payment gateway timed out")])
    webhooks = FakeWebhook()
    service = processing(gateway, webhooks)
    with pytest.raises(TransientProcessingError):
        await service.handle(unfinished, processing_token=None)

    await service.mark_exhausted(unfinished, "payment gateway timed out")

    status, delivered, reason = await payment_state(unfinished)
    assert webhooks.calls == 1
    assert webhooks.notifications[0].status == "failed"
    assert status == PaymentStatus.FAILED
    assert delivered is not None
    assert reason == "payment gateway timed out"

    finished = await create_payment()
    finished_webhooks = FakeWebhook()
    service = processing(FakeGateway(), finished_webhooks)
    await service.handle(finished, processing_token=None)
    await service.mark_exhausted(finished, "webhook returned 500")

    status, delivered, reason = await payment_state(finished)
    assert status == PaymentStatus.SUCCEEDED
    assert delivered is not None
    assert reason is None


async def test_failed_webhook_is_retried_after_exhaustion(
    create_payment: CreatePayment,
    processing: ProcessingFactory,
    payment_state: PaymentState,
) -> None:
    payment_id = await create_payment()
    webhooks = FakeWebhook(fail_times=2)
    service = processing(
        FakeGateway(),
        webhooks,
        webhook_retry_base_seconds=0,
        webhook_retry_max_seconds=0,
    )
    with pytest.raises(TransientProcessingError):
        await service.handle(payment_id, processing_token=None)

    await service.mark_exhausted(payment_id, "webhook returned 500")
    status, delivered, reason = await payment_state(payment_id)
    assert status == PaymentStatus.SUCCEEDED
    assert delivered is None
    assert reason is None
    assert webhooks.calls == 2

    assert await service.deliver_due_webhooks() == 1
    status, delivered, reason = await payment_state(payment_id)
    assert webhooks.calls == 3
    assert status == PaymentStatus.SUCCEEDED
    assert delivered is not None
    assert reason is None


@pytest.mark.commit
async def test_live_lease_blocks_a_second_charge(
    create_payment: CreatePayment,
    processing: ProcessingFactory,
) -> None:
    payment_id = await create_payment()
    gateway = _SlowGateway()
    service = processing(gateway, FakeWebhook(), lease_seconds=0.45)
    first = asyncio.create_task(service.handle(payment_id, processing_token=None))
    await gateway.started.wait()
    await asyncio.sleep(0.8)

    with pytest.raises(ProcessingBusy):
        await service.handle(payment_id, processing_token=None)

    assert gateway.calls == 1
    gateway.release.set()
    await first
    assert gateway.calls == 1


@pytest.mark.commit
async def test_gateway_call_does_not_keep_a_connection(
    create_payment: CreatePayment,
    processing: ProcessingFactory,
    checked_out: Callable[[], int],
) -> None:
    payment_id = await create_payment()
    gateway = _SlowGateway()
    task = asyncio.create_task(
        processing(gateway, FakeWebhook()).handle(
            payment_id,
            processing_token=None,
        ),
    )
    await gateway.started.wait()

    assert checked_out() == 0
    gateway.release.set()
    await task
    assert gateway.calls == 1


class _SlowGateway(FakeGateway):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def charge(
        self,
        payment_id: uuid.UUID,
        amount: Decimal,
        currency: str,
    ) -> GatewayChargeResult:
        del payment_id, amount, currency
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return self._result
