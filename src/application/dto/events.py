from __future__ import annotations

import uuid
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PaymentCreatedEvent:
    payment_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class DeadLetterEvent:
    payment_id: uuid.UUID
    error: str = "dead letter"
