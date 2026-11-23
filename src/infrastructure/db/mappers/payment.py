from src.domain.entities.payment import Payment
from src.domain.enums.payment import PaymentStatus
from src.infrastructure.db.models.payment import PaymentModel


def payment_to_entity(model: PaymentModel) -> Payment:
    status = model.status
    if not isinstance(status, PaymentStatus):
        status = PaymentStatus(str(status))
    return Payment(
        id=model.id,
        amount=model.amount,
        currency=model.currency,
        description=model.description,
        payment_metadata=dict(model.payment_metadata),
        status=status,
        idempotency_key=model.idempotency_key,
        webhook_url=model.webhook_url,
        failure_reason=model.failure_reason,
        provider_reference=model.provider_reference,
        processing_token=model.processing_token,
        processing_started_at=model.processing_started_at,
        webhook_delivered_at=model.webhook_delivered_at,
        webhook_attempts=model.webhook_attempts,
        webhook_next_retry_at=model.webhook_next_retry_at,
        processed_at=model.processed_at,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def payment_to_model(entity: Payment) -> PaymentModel:
    return PaymentModel(
        id=entity.id,
        amount=entity.amount,
        currency=entity.currency,
        description=entity.description,
        payment_metadata=entity.payment_metadata,
        status=entity.status,
        idempotency_key=entity.idempotency_key,
        webhook_url=entity.webhook_url,
        failure_reason=entity.failure_reason,
        provider_reference=entity.provider_reference,
        processing_token=entity.processing_token,
        processing_started_at=entity.processing_started_at,
        webhook_delivered_at=entity.webhook_delivered_at,
        webhook_attempts=entity.webhook_attempts,
        webhook_next_retry_at=entity.webhook_next_retry_at,
        processed_at=entity.processed_at,
        created_at=entity.created_at,
        updated_at=entity.updated_at,
    )


def apply_payment(entity: Payment, model: PaymentModel) -> None:
    model.amount = entity.amount
    model.currency = entity.currency
    model.description = entity.description
    model.payment_metadata = entity.payment_metadata
    model.status = entity.status
    model.idempotency_key = entity.idempotency_key
    model.webhook_url = entity.webhook_url
    model.failure_reason = entity.failure_reason
    model.provider_reference = entity.provider_reference
    model.processing_token = entity.processing_token
    model.processing_started_at = entity.processing_started_at
    model.webhook_delivered_at = entity.webhook_delivered_at
    model.webhook_attempts = entity.webhook_attempts
    model.webhook_next_retry_at = entity.webhook_next_retry_at
    model.processed_at = entity.processed_at
    model.created_at = entity.created_at
    model.updated_at = entity.updated_at
