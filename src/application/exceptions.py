class ApplicationError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class PaymentNotFound(ApplicationError):
    pass


class IdempotencyPayloadMismatch(ApplicationError):
    pass


class IdempotencyInProgress(ApplicationError):
    pass


class TransientProcessingError(ApplicationError):
    def __init__(self, message: str, *, processing_token: str) -> None:
        super().__init__(message)
        self.processing_token = processing_token


class ProcessingBusy(ApplicationError):
    def __init__(self, message: str, *, processing_token: str | None) -> None:
        super().__init__(message)
        self.processing_token = processing_token
