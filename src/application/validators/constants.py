import re

from src.domain.constants import IDEMPOTENCY_KEY_MAX_LENGTH

LOCAL_NAMES = frozenset({"localhost", "localhost.localdomain"})

IDEMPOTENCY_KEY_PATTERN = re.compile(
    rf"^[\x21-\x7e]{{1,{IDEMPOTENCY_KEY_MAX_LENGTH}}}$",
)
