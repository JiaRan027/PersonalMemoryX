"""Extractor Port: source text -> memory candidates.

Extractors NEVER write to the authoritative database. They only produce
CandidateItems; admission (accept/provisional/quarantine/restrict/discard)
remains entirely in the v0.1 IngestionService + AutoAdmissionPolicy path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from personalmemory.domain.memory.types import MemoryType

SOURCE_SIGNAL_DETERMINISTIC = "deterministic"
SOURCE_SIGNAL_LLM = "llm"


@dataclass(frozen=True)
class CandidateItem:
    type: MemoryType
    statement: str
    reason: str | None
    confidence: float
    importance: float
    evidence_span: tuple[int, int]
    source_signal: str
    extra: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ExtractionResult:
    items: list[CandidateItem]
    warnings: list[str] = field(default_factory=list)


class ExtractorPort(Protocol):
    def extract(
        self,
        content: str,
        *,
        source_revision_id: str,
        namespace: str,
    ) -> ExtractionResult: ...
