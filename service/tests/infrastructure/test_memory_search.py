from personalmemory.infrastructure.database.models import MemoryObjectRecord, MemorySpaceRecord
from personalmemory.infrastructure.database.search import MemorySearchIndex
from personalmemory.infrastructure.database.session import Database
from personalmemory.settings import Settings


def test_memory_search_index_finds_and_rebuilds_authoritative_objects(tmp_path):
    database = Database(Settings(data_dir=tmp_path))
    database.initialize()
    with database.session_factory.begin() as session:
        session.add(MemorySpaceRecord(id="space_default", name="Default"))
        session.flush()
        session.add(
            MemoryObjectRecord(
                id="memory_match",
                memory_space_id="space_default",
                type="insight",
                statement="Durable personal memory should survive model changes",
                reason="The memory layer is independent from providers",
                namespace="general",
                sensitivity="personal",
                epistemic_state="asserted",
                status="active",
                semantic_fingerprint="fingerprint-match",
                payload={},
                revision=1,
            )
        )

    index = MemorySearchIndex(database)
    assert index.search_ids("durable memory") == ["memory_match"]

    with database.session_factory.begin() as session:
        memory = session.get(MemoryObjectRecord, "memory_match")
        memory.statement = "Provider independent experience asset"

    assert index.search_ids("durable memory") == []
    assert index.search_ids("experience asset") == ["memory_match"]

    index.rebuild()
    assert index.search_ids("experience asset") == ["memory_match"]


def test_memory_search_finds_natural_chinese_multi_term_queries(tmp_path):
    database = Database(Settings(data_dir=tmp_path))
    database.initialize()
    memories = [
        MemoryObjectRecord(
            id="memory_goal",
            memory_space_id="space_default",
            type="goal",
            statement="打造个人 AI 记忆基础设施，让用户跨 AI 平台携带自己的长期经验资产。",
            reason="这是 PersonalMemory 的长期产品目标。",
            namespace="general",
            sensitivity="personal",
            epistemic_state="asserted",
            status="active",
            semantic_fingerprint="fingerprint-goal",
            payload={},
            revision=1,
        ),
        MemoryObjectRecord(
            id="memory_preference",
            memory_space_id="space_default",
            type="preference",
            statement="喜欢详细、工程化、能落地验证的分析与协作方式。",
            reason="项目 review 与开发协作中持续体现的偏好。",
            namespace="general",
            sensitivity="personal",
            epistemic_state="asserted",
            status="active",
            semantic_fingerprint="fingerprint-preference",
            payload={},
            revision=1,
        ),
        MemoryObjectRecord(
            id="memory_decision",
            memory_space_id="space_default",
            type="decision",
            statement="PersonalMemory 采用自部署优先路线，而不是以 SaaS 为默认形态。",
            reason="保护用户数据主权与可迁移性。",
            namespace="general",
            sensitivity="personal",
            epistemic_state="asserted",
            status="active",
            semantic_fingerprint="fingerprint-decision",
            payload={},
            revision=1,
        ),
    ]
    with database.session_factory.begin() as session:
        session.add(MemorySpaceRecord(id="space_default", name="Default"))
        session.flush()
        session.add_all(memories)

    index = MemorySearchIndex(database)

    assert index.search_ids("个人 AI 记忆 基础设施") == ["memory_goal"]
    assert index.search_ids("跨 AI 平台 长期 经验 资产") == ["memory_goal"]
    assert index.search_ids("详细 工程化 落地 验证") == ["memory_preference"]
    assert index.search_ids("协作 偏好") == ["memory_preference"]
    assert index.search_ids("自部署 优先") == ["memory_decision"]
    assert index.search_ids("数据 主权 可迁移性") == ["memory_decision"]
    assert index.search_ids("不存在 决策") == []
