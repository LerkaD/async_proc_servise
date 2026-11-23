from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from src.application.dto.events import PaymentCreatedEvent
from src.application.exceptions import ProcessingBusy, TransientProcessingError
from src.infrastructure.messaging.constants import (
    DLQ_ROUTING_KEY,
    HEADER_ATTEMPT,
    HEADER_PROCESSING_TOKEN,
)
from src.infrastructure.messaging.topology import (
    DLX_EXCHANGE,
    RETRY_QUEUES,
    retry_delay_seconds,
)
from src.infrastructure.workers.consumer import PaymentConsumer

pytestmark = pytest.mark.nodb


def test_retry_delay_is_exponential() -> None:
    assert retry_delay_seconds(1, base=2, cap=60) == 2
    assert retry_delay_seconds(2, base=2, cap=60) == 4
    assert retry_delay_seconds(3, base=2, cap=60) == 8
    assert retry_delay_seconds(10, base=2, cap=60) == 60


async def test_garbage_attempt_header_falls_back_to_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published = await _capture_republish(
        monkeypatch,
        headers={HEADER_ATTEMPT: "bad", HEADER_PROCESSING_TOKEN: "tok"},
        error=TransientProcessingError("webhook returned 500", processing_token="tok"),
    )
    headers = published[0]["headers"]
    assert isinstance(headers, dict)
    assert headers[HEADER_ATTEMPT] == 2


async def test_retry_keeps_the_processing_token(monkeypatch: pytest.MonkeyPatch) -> None:
    published = await _capture_republish(
        monkeypatch,
        headers={HEADER_ATTEMPT: 1, HEADER_PROCESSING_TOKEN: "tok-1"},
        error=TransientProcessingError("webhook returned 500", processing_token="tok-1"),
    )

    headers = published[0]["headers"]
    assert isinstance(headers, dict)
    assert headers[HEADER_ATTEMPT] == 2
    assert headers[HEADER_PROCESSING_TOKEN] == "tok-1"
    assert published[0]["queue"] is RETRY_QUEUES[0]


async def test_busy_delivery_does_not_consume_an_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published = await _capture_republish(
        monkeypatch,
        headers={HEADER_ATTEMPT: 2},
        error=ProcessingBusy("payment is being processed", processing_token="owner-tok"),
    )

    headers = published[0]["headers"]
    assert isinstance(headers, dict)
    assert headers[HEADER_ATTEMPT] == 2
    assert headers[HEADER_PROCESSING_TOKEN] == "owner-tok"
    assert published[0]["queue"] is RETRY_QUEUES[0]


async def test_last_attempt_is_published_to_the_dead_letter_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published = await _capture_republish(
        monkeypatch,
        headers={HEADER_ATTEMPT: 3, HEADER_PROCESSING_TOKEN: "tok"},
        error=TransientProcessingError("webhook returned 500", processing_token="tok"),
    )

    assert published[0]["exchange"] is DLX_EXCHANGE
    assert published[0]["routing_key"] == DLQ_ROUTING_KEY
    body = published[0]["body"]
    assert isinstance(body, dict)
    assert body["attempts"] == 3
    assert body["error"] == "webhook returned 500"


async def test_publish_error_is_not_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(body: object, **kwargs: object) -> None:
        del body, kwargs
        raise RuntimeError("confirm failed")

    worker = _worker(
        SimpleNamespace(publish=boom),
        _FailingProcessing(
            TransientProcessingError("webhook returned 500", processing_token="tok"),
        ),
    )

    with pytest.raises(RuntimeError, match="confirm failed"):
        await worker.handle_delivery(
            PaymentCreatedEvent(payment_id=uuid.uuid4()),
            SimpleNamespace(
                headers={HEADER_ATTEMPT: 1, HEADER_PROCESSING_TOKEN: "tok"},
            ),
        )


class _FailingProcessing:
    def __init__(self, error: Exception) -> None:
        self._error = error

    async def handle(self, payment_id: uuid.UUID, *, processing_token: str | None) -> None:
        del payment_id, processing_token
        raise self._error

    async def mark_exhausted(self, payment_id: uuid.UUID, reason: str) -> None:
        del payment_id, reason


def _worker(broker: object, processing: object) -> PaymentConsumer:
    return PaymentConsumer(processing, broker)  # type: ignore[arg-type]


async def _capture_republish(
    monkeypatch: pytest.MonkeyPatch,
    *,
    headers: dict[str, object],
    error: Exception,
) -> list[dict[str, object]]:
    del monkeypatch
    published: list[dict[str, object]] = []

    async def capture(body: object, **kwargs: object) -> None:
        published.append({"body": body, **kwargs})

    worker = _worker(SimpleNamespace(publish=capture), _FailingProcessing(error))
    await worker.handle_delivery(
        PaymentCreatedEvent(payment_id=uuid.uuid4()),
        SimpleNamespace(headers=headers),
    )
    return published
