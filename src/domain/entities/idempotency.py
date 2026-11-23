from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class IdempotencyKey:
    key: str
    request_hash: str
    response_body: dict[str, Any] | None
    status_code: int | None
    payment_id: uuid.UUID | None
    created_at: datetime
