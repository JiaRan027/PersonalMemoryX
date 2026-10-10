from personalmemory.infrastructure.database.models import (
    ActivityRecord,
    ConnectorInstanceRecord,
    MemoryCandidateRecord,
    MemoryObjectRecord,
    MemorySpaceRecord,
)
from personalmemory.infrastructure.database.session import Database
from personalmemory.settings import Settings
from sqlalchemy import func, select


def make_database(tmp_path) -> Database:
    database = Database(Settings(data_dir=tmp_path, api_token="test-token"))
    database.initialize()
    with database.session_factory.begin() as session:
        session.add(MemorySpaceRecord(id="space_01", name="我的记忆"))
        session.flush()
        session.add(
            ConnectorInstanceRecord(
                id="connector_01",
                memory_space_id="space_01",
                profile_id="generic_markdown_readonly",
                name="测试来源",
                source_path=str(tmp_path / "source.md"),
                namespace="general",
                can_read=True,
                can_write=False,
            )
        )
    return database


def test_ingestion_applies_all_automatic_admission_actions(tmp_path):
    from personalmemory.application.ingestion import IngestionService

    database = make_database(tmp_path)
    content = """\
## Fact
- 用户正在构建 PersonalMemory。
- 用户最近被诊断为高血压。
- api_token: SuperSecretValue123

## Goal
- 发布一个可运行的 Windows 首版。

## 随手记录
- 用户可能计划学习 Rust。
"""

    result = IngestionService(database).ingest(
        connector_id="connector_01",
        source_path=tmp_path / "source.md",
        content=content,
    )

    assert result.duplicate is False
    assert result.counts == {
        "accepted": 2,
        "provisional": 1,
        "quarantined": 0,
        "restricted": 1,
        "discarded": 1,
    }

    with database.session_factory() as session:
        memories = session.scalars(select(MemoryObjectRecord)).all()
        candidates = session.scalars(select(MemoryCandidateRecord)).all()
        activity = session.scalar(select(ActivityRecord))

    assert len(memories) == 4
    assert {memory.epistemic_state for memory in memories} == {"asserted", "inferred"}
    assert {memory.sensitivity for memory in memories} == {"personal", "sensitive"}
    discarded = next(candidate for candidate in candidates if candidate.action == "discard")
    assert discarded.draft is None
    assert {key: activity.details[key] for key in result.counts} == result.counts
    assert set(activity.details["memory_ids"]) == {memory.id for memory in memories}
    assert {item["id"] for item in activity.details["memory_changes"]} == {
        memory.id for memory in memories
    }
    assert all(item["action"] == "created" for item in activity.details["memory_changes"])
    assert activity.details["source_name"] == "测试来源"
    assert activity.details["source_profile"] == "generic_markdown_readonly"
    assert activity.details["source_locator"].endswith("source.md")
    assert b"SuperSecretValue123" not in database.settings.database_path.read_bytes()


def test_ingestion_is_idempotent_for_same_connector_and_hash(tmp_path):
    from personalmemory.application.ingestion import IngestionService

    database = make_database(tmp_path)
    service = IngestionService(database)
    content = "## Fact\n- 用户拥有自己的记忆数据。\n"

    first = service.ingest("connector_01", tmp_path / "source.md", content)
    second = service.ingest("connector_01", tmp_path / "source.md", content)

    with database.session_factory() as session:
        memory_count = session.scalar(select(func.count()).select_from(MemoryObjectRecord))

    assert first.duplicate is False
    assert second.duplicate is True
    assert second.source_revision_id == first.source_revision_id
    assert memory_count == 1


def test_ingestion_quarantines_deterministic_slot_conflict(tmp_path):
    from personalmemory.application.ingestion import IngestionService

    database = make_database(tmp_path)
    service = IngestionService(database)

    service.ingest(
        "connector_01",
        tmp_path / "source.md",
        "## Preference\n- Editor: VS Code\n",
    )
    result = service.ingest(
        "connector_01",
        tmp_path / "source.md",
        "## Preference\n- Editor: Zed\n",
    )

    with database.session_factory() as session:
        memories = session.scalars(select(MemoryObjectRecord)).all()
        quarantined = session.scalars(
            select(MemoryCandidateRecord).where(MemoryCandidateRecord.status == "quarantined")
        ).all()

    assert result.counts["quarantined"] == 1
    assert [memory.statement for memory in memories] == ["Editor: VS Code"]
    assert len(quarantined) == 1
    assert quarantined[0].draft["statement"] == "Editor: Zed"

def test_remember_is_idempotent_by_semantic_fingerprint(tmp_path):
    from personalmemory.application.ingestion import IngestionService
    from personalmemory.domain.memory.model import MemoryDraft
    from personalmemory.domain.memory.types import MemoryType

    database = make_database(tmp_path)
    service = IngestionService(database)
    draft = MemoryDraft(
        type=MemoryType.FACT,
        statement="用户正在构建 PersonalMemory",
        reason=None,
        source_revision_id="manual:agent_mcp",
        confidence=0.9,
        importance=0.5,
        namespace="general",
        payload={"source": "agent_mcp"},
    )

    first = service.remember("space_01", draft)
    second = service.remember("space_01", draft)

    assert first is not None
    assert second is not None
    assert first[0] == second[0]
    assert first[1].value == "provisional"
    assert first[2] is True
    assert second[1].value == "provisional"
    assert second[2] is False

    with database.session_factory() as session:
        count = session.scalar(select(func.count()).select_from(MemoryObjectRecord))
    assert count == 1


def test_identity_role_conflict_is_quarantined_without_silent_replacement(tmp_path):
    from personalmemory.application.ingestion import IngestionService
    from personalmemory.domain.memory.model import MemoryDraft
    from personalmemory.domain.memory.types import MemoryType

    database = make_database(tmp_path)
    service = IngestionService(database)

    first = MemoryDraft(
        type=MemoryType.FACT,
        statement="示例职业：工程师",
        reason=None,
        source_revision_id="manual:test",
        confidence=0.9,
        importance=0.5,
        namespace="general",
        payload={"semantic_slot": "self_identity:occupation"},
    )
    second = first.model_copy(
        update={"statement": "示例职业：园艺师", "source_revision_id": "manual:test-2"}
    )

    assert service.remember("space_01", first) is not None
    assert service.remember("space_01", second) is None

    with database.session_factory() as session:
        memories = session.scalars(select(MemoryObjectRecord)).all()

    assert [memory.statement for memory in memories] == ["示例职业：工程师"]


def test_unverified_identity_example_is_excluded_while_explicit_student_is_deliverable(
    tmp_path,
):
    from personalmemory.application.ingestion import IngestionService
    from personalmemory.application.review_triage import build_review_triage

    database = make_database(tmp_path)
    with database.session_factory.begin() as session:
        connector = session.get(ConnectorInstanceRecord, "connector_01")
        connector.profile_id = "conversation_text"

    service = IngestionService(database)
    service.ingest_content(
        "connector_01",
        source_locator="history-without-roles.txt",
        content="我是示例软件工程师。",
    )
    service.ingest_content(
        "connector_01",
        source_locator="verified-user-turn.txt",
        content="用户: 我是一名大学生。",
    )

    with database.session_factory() as session:
        memories = session.scalars(select(MemoryObjectRecord)).all()
        triage = build_review_triage(memories)

    assert len(memories) == 1
    student = memories[0]
    assert student.statement == "一名大学生"
    assert student.payload["semantic_slot"] == "self_identity:education"
    assert student.revision == 2
    assert triage[student.id]["effective_verdict"] == "keep"
    assert triage[student.id]["delivery_eligible"] is True


def test_explicit_user_identity_replaces_inferred_identity_and_preserves_history(tmp_path):
    from personalmemory.application.ingestion import IngestionService
    from personalmemory.domain.memory.model import MemoryDraft
    from personalmemory.domain.memory.types import MemoryType
    from personalmemory.infrastructure.database.models import (
        MemoryRevisionRecord,
        TombstoneRecord,
    )

    database = make_database(tmp_path)
    with database.session_factory.begin() as session:
        connector = session.get(ConnectorInstanceRecord, "connector_01")
        connector.profile_id = "conversation_text"

    service = IngestionService(database)
    inferred = MemoryDraft(
        type=MemoryType.FACT,
        statement="示例学习者",
        reason="根据长期讨论示例课程推断",
        source_revision_id="agent:inference",
        confidence=0.7,
        importance=0.6,
        namespace="general",
        payload={
            "semantic_slot": "self_identity:education",
            "semantic_authority": {
                "actor_kind": "agent",
                "assertion_kind": "inferred",
                "actor_id": "test-agent",
            },
        },
    )
    first = service.remember("space_01", inferred)
    assert first is not None
    result = service.ingest_content(
        "connector_01",
        source_locator="explicit-user-turn.txt",
        content="用户: 我是一名示例教师。",
    )
    assert result.counts["accepted"] == 1

    with database.session_factory() as session:
        memories = session.scalars(select(MemoryObjectRecord)).all()
        revisions = session.scalars(
            select(MemoryRevisionRecord).order_by(MemoryRevisionRecord.revision)
        ).all()
        tombstones = session.scalars(select(TombstoneRecord)).all()

    assert len(memories) == 1
    assert memories[0].statement == "一名示例教师"
    assert memories[0].revision == 2
    assert memories[0].epistemic_state == "asserted"
    assert [revision.snapshot["statement"] for revision in revisions] == [
        "示例学习者",
        "一名示例教师",
    ]
    assert any(item.object_kind == "superseded_revision" for item in tombstones)


def test_explicit_user_confirmation_upgrades_same_inferred_memory(tmp_path):
    from personalmemory.application.ingestion import IngestionService
    from personalmemory.domain.memory.model import MemoryDraft
    from personalmemory.domain.memory.types import MemoryType

    database = make_database(tmp_path)
    service = IngestionService(database)
    inferred = MemoryDraft(
        type=MemoryType.FACT,
        statement="示例职业：工程师",
        reason=None,
        source_revision_id="agent:inference",
        confidence=0.7,
        importance=0.6,
        namespace="general",
        payload={"semantic_slot": "self_identity:occupation"},
    )
    explicit = inferred.model_copy(
        update={
            "source_revision_id": "user:explicit",
            "payload": {
                "semantic_slot": "self_identity:occupation",
                "evidence_actor": "user",
                "semantic_authority": {
                    "actor_kind": "user",
                    "assertion_kind": "explicit",
                    "actor_id": None,
                },
            },
        }
    )

    assert service.remember("space_01", inferred) is not None
    result = service.remember("space_01", explicit)
    assert result is not None
    assert result[2] is True

    with database.session_factory() as session:
        memory = session.scalar(select(MemoryObjectRecord))

    assert memory is not None
    assert memory.revision == 2
    assert memory.epistemic_state == "asserted"


def test_remember_discards_tombstoned_fingerprint(tmp_path):
    from personalmemory.application.ingestion import IngestionService, semantic_fingerprint
    from personalmemory.domain.memory.model import MemoryDraft
    from personalmemory.domain.memory.types import MemoryType
    from personalmemory.infrastructure.database.models import TombstoneRecord

    database = make_database(tmp_path)
    draft = MemoryDraft(
        type=MemoryType.FACT,
        statement="用户正在构建 PersonalMemory",
        reason=None,
        source_revision_id="manual:agent_mcp",
        confidence=0.9,
        importance=0.5,
        namespace="general",
        payload={"source": "agent_mcp"},
    )
    with database.session_factory.begin() as session:
        session.add(
            TombstoneRecord(
                id="tombstone_01",
                memory_space_id="space_01",
                object_id="memory_removed",
                object_kind="memory",
                semantic_fingerprint=semantic_fingerprint(draft),
            )
        )

    result = IngestionService(database).remember("space_01", draft)

    assert result is None


def test_legacy_tombstone_blocks_nfkc_normalized_ingestion_and_remember(tmp_path):
    from personalmemory.application.ingestion import IngestionService, semantic_fingerprint
    from personalmemory.domain.memory.fingerprint import legacy_semantic_fingerprint_value
    from personalmemory.domain.memory.model import MemoryDraft
    from personalmemory.domain.memory.types import MemoryType
    from personalmemory.infrastructure.database.models import TombstoneRecord

    database = make_database(tmp_path)
    statement = "\u6211\u4f1a\uff1a"
    legacy_fingerprint = legacy_semantic_fingerprint_value(
        MemoryType.SKILL, "general", statement
    )
    with database.session_factory.begin() as session:
        session.add(
            TombstoneRecord(
                id="tombstone_legacy",
                memory_space_id="space_01",
                object_id="memory_removed",
                object_kind="memory",
                semantic_fingerprint=legacy_fingerprint,
            )
        )

    service = IngestionService(database)
    ingestion = service.ingest_content(
        "connector_01",
        source_locator="legacy-tombstone.md",
        content=f"## Skill\n- {statement}\n",
    )
    remembered = service.remember(
        "space_01",
        MemoryDraft(
            type=MemoryType.SKILL,
            statement=statement,
            reason=None,
            source_revision_id="manual:test",
            confidence=0.9,
            importance=0.5,
            namespace="general",
            payload={},
        ),
    )

    with database.session_factory() as session:
        candidates = session.scalars(select(MemoryCandidateRecord)).all()
        memory_count = session.scalar(select(func.count()).select_from(MemoryObjectRecord))

    assert legacy_fingerprint != semantic_fingerprint(
        MemoryDraft(
            type=MemoryType.SKILL,
            statement=statement,
            reason=None,
            source_revision_id="manual:test",
            confidence=0.9,
            importance=0.5,
            namespace="general",
            payload={},
        )
    )
    assert ingestion.counts["discarded"] == 1
    assert remembered is None
    assert memory_count == 0
    assert candidates[0].reason_code == "TOMBSTONED_MEMORY"


def test_legacy_fingerprint_existing_memory_remains_idempotent(tmp_path):
    from personalmemory.application.ingestion import IngestionService
    from personalmemory.domain.memory.fingerprint import legacy_semantic_fingerprint_value
    from personalmemory.domain.memory.model import MemoryDraft
    from personalmemory.domain.memory.types import AdmissionAction, MemoryType

    database = make_database(tmp_path)
    statement = "\u6211\u4f1a\uff1a"
    legacy_fingerprint = legacy_semantic_fingerprint_value(
        MemoryType.SKILL, "general", statement
    )
    with database.session_factory.begin() as session:
        session.add(
            MemoryObjectRecord(
                id="memory_legacy",
                memory_space_id="space_01",
                type=MemoryType.SKILL.value,
                statement=statement,
                reason=None,
                namespace="general",
                sensitivity="personal",
                epistemic_state="asserted",
                status="active",
                semantic_fingerprint=legacy_fingerprint,
                semantic_key=None,
                payload={},
                revision=1,
            )
        )

    draft = MemoryDraft(
        type=MemoryType.SKILL,
        statement=statement,
        reason=None,
        source_revision_id="manual:test",
        confidence=0.9,
        importance=0.5,
        namespace="general",
        payload={},
    )
    result = IngestionService(database).remember("space_01", draft)

    with database.session_factory() as session:
        memories = session.scalars(select(MemoryObjectRecord)).all()

    assert result == ("memory_legacy", AdmissionAction.ACCEPT, False)
    assert len(memories) == 1


def test_claude_technical_feedback_is_an_unconfirmed_insight(tmp_path):
    from personalmemory.application.ingestion import IngestionService

    database = make_database(tmp_path)
    with database.session_factory.begin() as session:
        connector = session.get(ConnectorInstanceRecord, "connector_01")
        connector.profile_id = "claude_memory_markdown"
        connector.namespace = "claude_code"

    content = """---
name: ArkTS @Builder requires this prefix
description: @Builder methods must use this when passed to .tabBar()
type: feedback
---
In ArkTS, when calling a `@Builder` method, you MUST use the `this.` prefix.

**Why:** The compiler treats bare method names as missing identifiers.
"""
    result = IngestionService(database).ingest_content(
        "connector_01",
        source_locator="feedback_arkts_builder_this.md",
        content=content,
    )

    with database.session_factory() as session:
        memory = session.scalar(select(MemoryObjectRecord))
        candidate = session.scalar(select(MemoryCandidateRecord))

    assert result.counts["provisional"] == 1
    assert memory.type == "insight"
    assert memory.epistemic_state == "inferred"
    assert memory.payload["classification_original_type"] == "preference"
    assert (
        memory.payload["classification_reason_code"]
        == "TECHNICAL_RULE_NOT_USER_PREFERENCE"
    )
    assert candidate.reason_code == "UNCONFIRMED_OBSERVATION"


def test_cross_namespace_exact_duplicate_is_preserved_and_marked_for_review(tmp_path):
    from personalmemory.application.ingestion import IngestionService

    database = make_database(tmp_path)
    with database.session_factory.begin() as session:
        session.add(
            ConnectorInstanceRecord(
                id="connector_02",
                memory_space_id="space_01",
                profile_id="generic_markdown_readonly",
                name="第二来源",
                source_path=str(tmp_path / "source-2.md"),
                namespace="projects/other",
                can_read=True,
                can_write=False,
            )
        )

    service = IngestionService(database)
    content = "## Fact\n- 用户重视数据主权。\n"
    first = service.ingest_content(
        "connector_01", source_locator="source-1.md", content=content
    )
    second = service.ingest_content(
        "connector_02", source_locator="source-2.md", content=content
    )

    with database.session_factory() as session:
        memories = session.scalars(
            select(MemoryObjectRecord).order_by(MemoryObjectRecord.created_at)
        ).all()
        second_candidate = session.scalars(
            select(MemoryCandidateRecord).order_by(MemoryCandidateRecord.created_at)
        ).all()[-1]

    assert first.counts["accepted"] == 1
    assert second.counts["provisional"] == 1
    assert len(memories) == 2
    assert memories[0].exact_content_fingerprint == memories[1].exact_content_fingerprint
    assert memories[1].epistemic_state == "inferred"
    assert memories[1].payload["quality_issues"] == [
        {
            "code": "CROSS_NAMESPACE_EXACT_DUPLICATE",
            "related_memory_id": memories[0].id,
            "related_namespace": "general",
        }
    ]
    assert second_candidate.reason_code == "CROSS_NAMESPACE_EXACT_DUPLICATE"
