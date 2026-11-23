from __future__ import annotations

import uuid
from decimal import Decimal

from fastapi import status

from src.domain.value_objects.gateway import GatewayChargeResult
from src.domain.value_objects.webhook import WebhookNotification


class FakeGateway:
    def __init__(
        self,
        *,
        result: GatewayChargeResult | None = None,
        errors: list[Exception] | None = None,
    ) -> None:
        self.calls = 0
        self._result = result or GatewayChargeResult(
            ok=True,
            error_code=None,
            provider_reference="gw_test",
        )
        self._errors = list(errors or [])

    async def charge(
        self,
        payment_id: uuid.UUID,
        amount: Decimal,
        currency: str,
    ) -> GatewayChargeResult:
        del payment_id, amount, currency
        self.calls += 1
        if self._errors:
            raise self._errors.pop(0)
        return self._result


class FakeWebhook:
    def __init__(self, *, fail_times: int = 0) -> None:
        self.calls = 0
        self.fail_times = fail_times
        self.notifications: list[WebhookNotification] = []

    async def send(self, url: str, notification: WebhookNotification) -> None:
        del url
        self.calls += 1
        self.notifications.append(notification)
        if self.calls <= self.fail_times:
            raise RuntimeError(
                f"webhook returned {status.HTTP_500_INTERNAL_SERVER_ERROR}",
            )
