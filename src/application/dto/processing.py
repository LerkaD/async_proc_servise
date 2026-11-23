from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from src.application.enums.processing import Step
from src.domain.entities.payment import Payment


@dataclass(frozen=True, slots=True)
class Snapshot:
    id: uuid.UUID
    amount: Decimal
    currency: str
    status: str
    webhook_url: str
    processed_at: datetime | None
    failure_reason: str | None

    @classmethod
    def from_payment(cls, payment: Payment) -> Snapshot:
        return cls(
            id=payment.id,
            amount=payment.amount,
            currency=payment.currency,
            status=payment.status.value,
            webhook_url=payment.webhook_url,
            processed_at=payment.processed_at,
            failure_reason=payment.failure_reason,
        )


@dataclass(frozen=True, slots=True)
class Decision:
    step: Step
    token: str
    snapshot: Snapshot
