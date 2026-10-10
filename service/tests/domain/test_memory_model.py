import pytest
from pydantic import ValidationError


@pytest.mark.parametrize(
    "memory_type",
    ["fact", "goal", "skill", "preference", "event", "insight", "decision"],
)
def test_memory_draft_supports_all_core_types(memory_type):
    from personalmemory.domain.memory.model import MemoryDraft

    draft = MemoryDraft(
        type=memory_type,
        statement="  用户正在构建 PersonalMemory。  ",
        reason="项目背景",
        source_revision_id="source_01",
        confidence=0.9,
        importance=0.8,
        namespace="projects/personalmemory",
    )

    assert draft.type.value == memory_type
    assert draft.statement == "用户正在构建 PersonalMemory。"


def test_memory_draft_rejects_empty_statement():
    from personalmemory.domain.memory.model import MemoryDraft

    with pytest.raises(ValidationError):
        MemoryDraft(
            type="fact",
            statement="   ",
            source_revision_id="source_01",
            confidence=0.9,
            importance=0.5,
            namespace="general",
        )
