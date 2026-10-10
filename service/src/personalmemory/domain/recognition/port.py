from __future__ import annotations

from typing import Protocol

from personalmemory.domain.recognition.models import RecognitionResult


class AgentMemoryRecognizerPort(Protocol):
    """Read-only discovery of local agent memory sources."""

    def recognize(self) -> RecognitionResult: ...
