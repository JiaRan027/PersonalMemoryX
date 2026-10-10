from enum import StrEnum


class MemoryType(StrEnum):
    FACT = "fact"
    GOAL = "goal"
    SKILL = "skill"
    PREFERENCE = "preference"
    EVENT = "event"
    INSIGHT = "insight"
    DECISION = "decision"


class AdmissionAction(StrEnum):
    ACCEPT = "accept"
    PROVISIONAL = "provisional"
    QUARANTINE = "quarantine"
    RESTRICT = "restrict"
    DISCARD = "discard"


class EpistemicState(StrEnum):
    ASSERTED = "asserted"
    INFERRED = "inferred"
    DISPUTED = "disputed"


class Sensitivity(StrEnum):
    PUBLIC = "public"
    PERSONAL = "personal"
    SENSITIVE = "sensitive"
    SECRET = "secret"
