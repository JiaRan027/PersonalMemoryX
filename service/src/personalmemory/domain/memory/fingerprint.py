from __future__ import annotations

import hashlib
import unicodedata

from personalmemory.domain.memory.types import MemoryType


def normalize_memory_statement(value: str) -> str:
    """Normalize exact semantic content without removing meaningful punctuation."""
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(normalized.split())


def legacy_normalize_memory_statement(value: str) -> str:
    """Match the pre-NFKC semantic fingerprint normalization."""
    return " ".join(value.casefold().split())


def semantic_fingerprint_value(
    memory_type: MemoryType | str,
    namespace: str,
    statement: str,
) -> str:
    type_value = memory_type.value if isinstance(memory_type, MemoryType) else memory_type
    value = (
        f"{type_value}|{namespace.casefold()}|{normalize_memory_statement(statement)}"
    )
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def legacy_semantic_fingerprint_value(
    memory_type: MemoryType | str,
    namespace: str,
    statement: str,
) -> str:
    """Return the semantic fingerprint used before NFKC normalization.

    This is retained only to match pre-normalization tombstones during
    admission; newly stored memories always use ``semantic_fingerprint_value``.
    """
    type_value = memory_type.value if isinstance(memory_type, MemoryType) else memory_type
    value = (
        f"{type_value}|{namespace.casefold()}|{legacy_normalize_memory_statement(statement)}"
    )
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def exact_content_fingerprint(
    memory_type: MemoryType | str,
    statement: str,
) -> str:
    """Fingerprint equal content across namespaces without changing scope semantics."""
    type_value = memory_type.value if isinstance(memory_type, MemoryType) else memory_type
    value = f"{type_value}|{normalize_memory_statement(statement)}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
