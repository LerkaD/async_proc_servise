from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from src.domain.enums.outbox import OutboxStatus


@dataclass(slots=True)
class OutboxEvent:
    id: uuid.UUID
    aggregate_id: uuid.UUID
    event_type: str
    payload: dict[str, Any]
    status: OutboxStatus
    retry_count: int
    next_retry_at: datetime | None
    last_error: str | None
    created_at: datetime
    published_at: datetime | None
