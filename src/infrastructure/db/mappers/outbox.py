from src.domain.entities.outbox import OutboxEvent
from src.domain.enums.outbox import OutboxStatus
from src.infrastructure.db.models.outbox import OutboxEventModel


def outbox_to_entity(model: OutboxEventModel) -> OutboxEvent:
    status = model.status
    if not isinstance(status, OutboxStatus):
        status = OutboxStatus(str(status))
    payload = model.payload
    body = dict(payload) if isinstance(payload, dict) else {}
    return OutboxEvent(
        id=model.id,
        aggregate_id=model.aggregate_id,
        event_type=model.event_type,
        payload=body,
        status=status,
        retry_count=model.retry_count,
        next_retry_at=model.next_retry_at,
        last_error=model.last_error,
        created_at=model.created_at,
        published_at=model.published_at,
    )


def outbox_to_model(entity: OutboxEvent) -> OutboxEventModel:
    return OutboxEventModel(
        id=entity.id,
        aggregate_id=entity.aggregate_id,
        event_type=entity.event_type,
        payload=entity.payload,
        status=entity.status,
        retry_count=entity.retry_count,
        next_retry_at=entity.next_retry_at,
        last_error=entity.last_error,
        created_at=entity.created_at,
        published_at=entity.published_at,
    )


def apply_outbox(entity: OutboxEvent, model: OutboxEventModel) -> None:
    model.aggregate_id = entity.aggregate_id
    model.event_type = entity.event_type
    model.payload = entity.payload
    model.status = entity.status
    model.retry_count = entity.retry_count
    model.next_retry_at = entity.next_retry_at
    model.last_error = entity.last_error
    model.created_at = entity.created_at
    model.published_at = entity.published_at
