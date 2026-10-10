from personalmemory.infrastructure.database.models import (
    ConnectorInstanceRecord,
    LoopGuardRecord,
    MemoryObjectRecord,
    MemorySpaceRecord,
    ProjectionReceiptRecord,
)
from personalmemory.infrastructure.database.session import Database
from personalmemory.settings import Settings
from sqlalchemy import func, select


def make_projection_database(tmp_path) -> tuple[Database, object]:
    target = tmp_path / "TARGET.md"
    target.write_text(
        "# Agent notes\n\n<!-- personalmemory:start -->\nold\n"
        "<!-- personalmemory:end -->\n",
        encoding="utf-8",
        newline="",
    )
    database = Database(Settings(data_dir=tmp_path / "data", api_token="test-token"))
    database.initialize()
    with database.session_factory.begin() as session:
        session.add(MemorySpaceRecord(id="space_01", name="我的记忆"))
        session.flush()
        session.add(
            ConnectorInstanceRecord(
                id="target_01",
                memory_space_id="space_01",
                profile_id="generic_markdown_managed",
                name="目标 Agent",
                target_path=str(target),
                namespace="general",
                can_read=False,
                can_write=True,
            )
        )
        session.flush()
        records = [
            MemoryObjectRecord(
                id="memory_fact",
                memory_space_id="space_01",
                type="fact",
                statement="用户正在构建 PersonalMemory。",
                reason=None,
                namespace="general",
                sensitivity="personal",
                epistemic_state="asserted",
                status="active",
                semantic_fingerprint="a" * 64,
                semantic_key=None,
                payload={},
                revision=1,
            ),
            MemoryObjectRecord(
                id="memory_decision",
                memory_space_id="space_01",
                type="decision",
                statement="首版使用结构化查询。",
                reason="大量个人记忆属于明确事实。",
                namespace="general",
                sensitivity="personal",
                epistemic_state="asserted",
                status="active",
                semantic_fingerprint="b" * 64,
                semantic_key=None,
                payload={},
                revision=2,
            ),
            MemoryObjectRecord(
                id="memory_provisional",
                memory_space_id="space_01",
                type="skill",
                statement="用户可能熟悉 Rust。",
                reason=None,
                namespace="general",
                sensitivity="personal",
                epistemic_state="inferred",
                status="active",
                semantic_fingerprint="c" * 64,
                semantic_key=None,
                payload={},
                revision=1,
            ),
            MemoryObjectRecord(
                id="memory_sensitive",
                memory_space_id="space_01",
                type="fact",
                statement="用户有私人健康信息。",
                reason=None,
                namespace="general",
                sensitivity="sensitive",
                epistemic_state="asserted",
                status="active",
                semantic_fingerprint="d" * 64,
                semantic_key=None,
                payload={},
                revision=1,
            ),
        ]
        session.add_all(records)
    return database, target


def test_projection_filters_risk_and_preserves_decision_reason(tmp_path):
    from personalmemory.application.projection import ProjectionService

    database, target = make_projection_database(tmp_path)
    service = ProjectionService(database)

    plan = service.plan("target_01")
    result = service.project("target_01")

    assert [item.memory_id for item in plan.items] == ["memory_decision", "memory_fact"]
    assert "用户正在构建 PersonalMemory。" in result.rendered_block
    assert "首版使用结构化查询。" in result.rendered_block
    assert "大量个人记忆属于明确事实。" in result.rendered_block
    assert "可能熟悉 Rust" not in result.rendered_block
    assert "私人健康信息" not in result.rendered_block
    assert result.projection_id in target.read_text(encoding="utf-8")

    with database.session_factory() as session:
        receipt_count = session.scalar(select(func.count()).select_from(ProjectionReceiptRecord))
        guard_count = session.scalar(select(func.count()).select_from(LoopGuardRecord))

    assert receipt_count == 1
    assert guard_count == 1


def make_manifest_database(tmp_path) -> tuple[Database, object]:
    target = tmp_path / "AGENT_CONTEXT.md"
    target.write_text(
        "# Agent-owned notes\n\n<!-- personalmemory:start -->\nold\n"
        "<!-- personalmemory:end -->\n\nKeep this line.\n",
        encoding="utf-8",
    )
    database = Database(Settings(data_dir=tmp_path / "manifest-data", api_token="test-token"))
    database.initialize()
    with database.session_factory.begin() as session:
        session.add(MemorySpaceRecord(id="space_manifest", name="连续性"))
        session.flush()
        session.add(
            ConnectorInstanceRecord(
                id="manifest_01",
                memory_space_id="space_manifest",
                profile_id="continuity_manifest_file",
                name="L0 startup cache",
                target_path=str(target),
                namespace="projects/personalmemory",
                can_read=False,
                can_write=True,
                read_enabled=False,
                write_enabled=True,
            )
        )
        session.add_all(
            [
                MemoryObjectRecord(
                    id="identity_general",
                    memory_space_id="space_manifest",
                    type="fact",
                    statement="用户是 PersonalMemory 的产品负责人。",
                    reason=None,
                    namespace="general",
                    sensitivity="personal",
                    epistemic_state="asserted",
                    status="active",
                    semantic_fingerprint="1" * 64,
                    semantic_key=None,
                    payload={},
                    revision=1,
                ),
                MemoryObjectRecord(
                    id="goal_current",
                    memory_space_id="space_manifest",
                    type="goal",
                    statement="当前目标是验证跨 Agent 连续性。",
                    reason="Agent 可替换，Continuity 不可替换。",
                    namespace="projects/personalmemory",
                    sensitivity="personal",
                    epistemic_state="asserted",
                    status="active",
                    semantic_fingerprint="2" * 64,
                    semantic_key=None,
                    payload={},
                    revision=2,
                ),
                MemoryObjectRecord(
                    id="preference_general",
                    memory_space_id="space_manifest",
                    type="preference",
                    statement="用户希望结论有证据并明确未知边界。",
                    reason=None,
                    namespace="general",
                    sensitivity="public",
                    epistemic_state="asserted",
                    status="active",
                    semantic_fingerprint="3" * 64,
                    semantic_key=None,
                    payload={},
                    revision=1,
                ),
                MemoryObjectRecord(
                    id="decision_excluded",
                    memory_space_id="space_manifest",
                    type="decision",
                    statement="这条决策只能通过 L1/L2 按需取回。",
                    reason=None,
                    namespace="projects/personalmemory",
                    sensitivity="personal",
                    epistemic_state="asserted",
                    status="active",
                    semantic_fingerprint="4" * 64,
                    semantic_key=None,
                    payload={},
                    revision=1,
                ),
                MemoryObjectRecord(
                    id="operational_fact_excluded",
                    memory_space_id="space_manifest",
                    type="fact",
                    statement="受管区外的正文必须在每次投影后保留。",
                    reason=None,
                    namespace="general",
                    sensitivity="personal",
                    epistemic_state="asserted",
                    status="active",
                    semantic_fingerprint="8" * 64,
                    semantic_key=None,
                    payload={},
                    revision=1,
                ),
                MemoryObjectRecord(
                    id="other_namespace",
                    memory_space_id="space_manifest",
                    type="goal",
                    statement="另一个项目的目标不能进入本项目 L0。",
                    reason=None,
                    namespace="projects/other",
                    sensitivity="personal",
                    epistemic_state="asserted",
                    status="active",
                    semantic_fingerprint="5" * 64,
                    semantic_key=None,
                    payload={},
                    revision=1,
                ),
                MemoryObjectRecord(
                    id="sensitive_excluded",
                    memory_space_id="space_manifest",
                    type="fact",
                    statement="敏感身份信息不能进入标准 L0。",
                    reason=None,
                    namespace="general",
                    sensitivity="sensitive",
                    epistemic_state="asserted",
                    status="active",
                    semantic_fingerprint="6" * 64,
                    semantic_key=None,
                    payload={},
                    revision=1,
                ),
                MemoryObjectRecord(
                    id="inferred_excluded",
                    memory_space_id="space_manifest",
                    type="preference",
                    statement="Agent 推测不能成为稳定启动偏好。",
                    reason=None,
                    namespace="general",
                    sensitivity="personal",
                    epistemic_state="inferred",
                    status="active",
                    semantic_fingerprint="7" * 64,
                    semantic_key=None,
                    payload={},
                    revision=1,
                ),
            ]
        )
    return database, target


def test_continuity_manifest_is_bounded_scoped_and_managed(tmp_path):
    from personalmemory.application.context import estimate_tokens
    from personalmemory.application.projection import ProjectionService

    database, target = make_manifest_database(tmp_path)
    service = ProjectionService(database)

    plan = service.plan("manifest_01")
    result = service.project("manifest_01")

    assert plan.mode == "continuity_manifest"
    assert plan.token_budget == 800
    assert {item.memory_id for item in plan.items} == {
        "identity_general",
        "goal_current",
        "preference_general",
    }
    assert "manifest_version: 2" in result.rendered_block
    assert "Identity and Active Context" in result.rendered_block
    assert "Current Goals" in result.rendered_block
    assert "Collaboration Preferences" in result.rendered_block
    assert "This is a compact startup context" in result.rendered_block
    assert "Treat this manifest as routing context" in result.rendered_block
    assert "a task that resembles a previous project's problem shape" in result.rendered_block
    assert "do not wait for the user to mention memory" in result.rendered_block
    assert "这条决策只能通过 L1/L2" not in result.rendered_block
    assert "受管区外的正文" not in result.rendered_block
    assert "另一个项目" not in result.rendered_block
    assert "敏感身份" not in result.rendered_block
    assert "Agent 推测" not in result.rendered_block
    assert estimate_tokens(result.rendered_block) <= 800
    written = target.read_text(encoding="utf-8")
    assert "# Agent-owned notes" in written
    assert "Keep this line." in written


def test_continuity_manifest_protocol_change_invalidates_projection_id(
    tmp_path, monkeypatch
):
    from personalmemory.application import projection as projection_module

    database, _ = make_manifest_database(tmp_path)
    original = projection_module.ProjectionService(database).plan("manifest_01")

    monkeypatch.setattr(
        projection_module,
        "MANIFEST_PROTOCOL_LINES",
        (*projection_module.MANIFEST_PROTOCOL_LINES, "A new routing rule."),
    )
    changed = projection_module.ProjectionService(database).plan("manifest_01")

    assert changed.projection_id != original.projection_id


def test_continuity_manifest_never_exceeds_its_token_budget(tmp_path):
    from personalmemory.application.context import estimate_tokens
    from personalmemory.application.projection import ProjectionService

    database, _ = make_manifest_database(tmp_path)
    with database.session_factory.begin() as session:
        for index in range(30):
            memory_type = ("fact", "goal", "preference")[index % 3]
            session.add(
                MemoryObjectRecord(
                    id=f"bulk_{index:02d}",
                    memory_space_id="space_manifest",
                    type=memory_type,
                    statement=f"第 {index} 条稳定连续性信息：" + "重要上下文" * 18,
                    reason=None,
                    namespace="general",
                    sensitivity="personal",
                    epistemic_state="asserted",
                    status="active",
                    semantic_fingerprint=f"{index + 20:064x}",
                    semantic_key=None,
                    payload={},
                    revision=1,
                )
            )

    service = ProjectionService(database)
    plan = service.plan("manifest_01")
    rendered = service.render(plan)

    assert plan.items
    assert len(plan.items) < 33
    assert all(item.statement in rendered for item in plan.items)
    assert estimate_tokens(rendered) <= 800


def test_continuity_manifest_propagates_memory_deletion(tmp_path):
    from personalmemory.application.projection import ProjectionService
    from personalmemory.application.undo import MemoryOperations

    database, target = make_manifest_database(tmp_path)
    ProjectionService(database).project("manifest_01")
    assert "当前目标是验证跨 Agent 连续性" in target.read_text(encoding="utf-8")

    MemoryOperations(database).delete("goal_current")

    updated = target.read_text(encoding="utf-8")
    assert "当前目标是验证跨 Agent 连续性" not in updated
    assert "用户是 PersonalMemory 的产品负责人" in updated
    assert "Keep this line." in updated
