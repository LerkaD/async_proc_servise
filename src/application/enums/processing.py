from enum import StrEnum


class Step(StrEnum):
    DONE = "done"
    CHARGE = "charge"
    WEBHOOK = "webhook"


class Lease(StrEnum):
    HELD = "held"
    DONE = "done"
    LOST = "lost"
