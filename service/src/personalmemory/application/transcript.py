from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from personalmemory.application.envelope import ContinuityEnvelopeService
from personalmemory.application.ingestion import IngestionService, content_hash
from personalmemory.infrastructure.filesystem.adapters import (
    ContinuityTranscript,
    decode_continuity_transcript,
)


@dataclass(frozen=True)
class TranscriptIngestionResult:
    file_digest: str
    envelopes: int
    duplicate_envelopes: int
    counts: dict[str, int]


class TranscriptIngestionService:
    """Admit transcript envelopes through the same extraction and policy Core."""

    def __init__(self, database, settings=None) -> None:
        self.database = database
        self.settings = settings

    def ingest_file(
        self,
        connector_id: str,
        source_path: Path,
        *,
        format_name: str,
        source_family: str,
    ) -> TranscriptIngestionResult:
        transcript = decode_continuity_transcript(
            source_path,
            format_name=format_name,
            source_family=source_family,
        )
        return self.ingest_transcript(connector_id, transcript)

    def ingest_transcript(
        self, connector_id: str, transcript: ContinuityTranscript
    ) -> TranscriptIngestionResult:
        total = IngestionService._empty_counts()
        duplicates = 0
        ingestion = ContinuityEnvelopeService(self.database, settings=self.settings)
        for envelope in transcript.envelopes:
            result = ingestion.ingest(
                connector_id,
                envelope,
                source_format=transcript.source_format,
            )
            duplicates += int(result.duplicate)
            for key, count in result.counts.items():
                total[key] += count
        return TranscriptIngestionResult(
            file_digest=content_hash(transcript.normalized_content()),
            envelopes=len(transcript.envelopes),
            duplicate_envelopes=duplicates,
            counts=total,
        )
