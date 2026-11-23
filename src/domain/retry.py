def retry_delay_seconds(attempt: int, *, base: float, cap: float) -> float:
    if attempt < 1:
        raise ValueError("attempt must be >= 1")
    shift = min(attempt - 1, 16)
    delay = base * float(2**shift)
    if delay > cap:
        return cap
    return delay
