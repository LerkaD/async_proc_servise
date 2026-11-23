from src.domain.entities.idempotency import IdempotencyKey
from src.infrastructure.db.models.idempotency import IdempotencyKeyModel


def idempotency_to_entity(model: IdempotencyKeyModel) -> IdempotencyKey:
    body = model.response_body
    return IdempotencyKey(
        key=model.key,
        request_hash=model.request_hash,
        response_body=dict(body) if isinstance(body, dict) else body,
        status_code=model.status_code,
        payment_id=model.payment_id,
        created_at=model.created_at,
    )


def apply_idempotency(entity: IdempotencyKey, model: IdempotencyKeyModel) -> None:
    model.request_hash = entity.request_hash
    model.response_body = entity.response_body
    model.status_code = entity.status_code
    model.payment_id = entity.payment_id
    model.created_at = entity.created_at
