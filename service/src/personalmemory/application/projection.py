from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

from personalmemory.application.context import estimate_tokens
from personalmemory.application.ingestion import new_id
from personalmemory.domain.connector.profile import get_builtin_profile
from personalmemory.infrastructure.database.models import (
    ActivityRecord,
    ConnectorInstanceRecord,
    LoopGuardRecord,
    MemoryObjectRecord,
    ProjectionReceiptRecord,
)
from personalmemory.infrastructure.database.session import Database
from personalmemory.infrastructure.filesystem.writer import ManagedBlockWriter, hash_bytes

TYPE_LABELS = {
    "fact": "Fact",
    "goal": "Goal",
    "skill": "Skill",
    "preference": "Preference",
    "event": "Event",
    "insight": "Insight",
    "decision": "Decision",
}

MANIFEST_VERSION = "2"
MANIFEST_TYPES = ("fact", "goal", "preference")
MANIFEST_TYPE_LABELS = {
    "fact": "Identity and Active Context",
    "goal": "Current Goals",
    "preference": "Collaboration Preferences",
}
MANIFEST_PROTOCOL_LINES = (
    "This is a compact startup context, not the complete memory corpus.",
    "Treat this manifest as routing context, not evidence for task-specific decisions.",
    "Before architecture or tradeoff work, historical questions, or a task that resembles a "
    "previous project's problem shape, query PersonalMemory for relevant decisions, insights, "
    "and experience; do not wait for the user to mention memory.",
    "When PersonalMemory has no supporting evidence, say you do not know.",
    "Submit only durable, user-confirmed changes to PersonalMemory; do not promote Agent "
    "inference to user authority.",
)
MANIFEST_FACT_PATTERN = re.compile(
    r"^(?:用户(?:是|正在|最近正在|目前正在|从事|居住|使用|主要)|"
    r"我(?:是|正在|最近正在|目前正在|从事|住在|使用|主要)|"
    r"the user (?:is|works|uses|lives)|i (?:am|work|use|live))",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ProjectionItem:
    memory_id: str
    revision: int
    type: str
    statement: str
    reason: str | None


@dataclass(frozen=True)
class ProjectionPlan:
    connector_id: str
    projection_id: str
    items: list[ProjectionItem]
    mode: str = "full"
    token_budget: int | None = None


@dataclass(frozen=True)
class ProjectionResult:
    projection_id: str
    rendered_block: str
    file_hash: str
    duplicate: bool


class ProjectionService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def plan(self, connector_id: str) -> ProjectionPlan:
        with self.database.session_factory() as session:
            connector = self._get_connector(session, connector_id)
            if not connector.can_write:
                raise PermissionError(f"Connector cannot write projections: {connector_id}")
            profile = get_builtin_profile(connector.profile_id)
            query = select(MemoryObjectRecord).where(
                MemoryObjectRecord.memory_space_id == connector.memory_space_id,
                MemoryObjectRecord.status == "active",
                MemoryObjectRecord.sensitivity.in_(("public", "personal")),
                MemoryObjectRecord.epistemic_state == "asserted",
            )
            if profile.projection_mode == "continuity_manifest":
                namespaces = {"general", connector.namespace}
                query = (
                    query.where(
                        MemoryObjectRecord.type.in_(MANIFEST_TYPES),
                        MemoryObjectRecord.namespace.in_(namespaces),
                    )
                    .order_by(MemoryObjectRecord.updated_at.desc(), MemoryObjectRecord.id)
                    .limit(256)
                )
            else:
                query = query.order_by(
                    MemoryObjectRecord.type, MemoryObjectRecord.statement
                )
            rows = session.scalars(query).all()

        candidates = [
            ProjectionItem(
                memory_id=row.id,
                revision=row.revision,
                type=row.type,
                statement=row.statement,
                reason=row.reason,
            )
            for row in rows
        ]
        items = (
            self._select_manifest_items(candidates, profile.projection_token_budget or 800)
            if profile.projection_mode == "continuity_manifest"
            else candidates
        )
        render_contract = profile.projection_mode
        if profile.projection_mode == "continuity_manifest":
            render_contract = "|".join(
                [
                    profile.projection_mode,
                    MANIFEST_VERSION,
                    *(f"{key}:{value}" for key, value in MANIFEST_TYPE_LABELS.items()),
                    *MANIFEST_PROTOCOL_LINES,
                ]
            )
        identity = "|".join(
            [
                connector_id,
                render_contract,
                *(f"{item.memory_id}:{item.revision}" for item in items),
            ]
        )
        projection_id = f"projection_{hashlib.sha256(identity.encode()).hexdigest()[:24]}"
        return ProjectionPlan(
            connector_id=connector_id,
            projection_id=projection_id,
            items=items,
            mode=profile.projection_mode,
            token_budget=profile.projection_token_budget,
        )

    def render(self, plan: ProjectionPlan) -> str:
        if plan.mode == "continuity_manifest":
            return self._render_manifest(plan)
        lines = [
            "<!-- personalmemory:start -->",
            f"<!-- projection_id: {plan.projection_id} -->",
            "# PersonalMemory",
            "",
        ]
        current_type: str | None = None
        for item in plan.items:
            if item.type != current_type:
                if current_type is not None:
                    lines.append("")
                lines.append(f"## {TYPE_LABELS.get(item.type, item.type.title())}")
                lines.append("")
                current_type = item.type
            lines.append(f"- {item.statement}")
            if item.reason:
                lines.append(f"  - 原因：{item.reason}")
        if plan.items:
            lines.append("")
        lines.append("<!-- personalmemory:end -->")
        return "\n".join(lines)

    def _select_manifest_items(
        self, candidates: list[ProjectionItem], token_budget: int
    ) -> list[ProjectionItem]:
        candidates = [
            item
            for item in candidates
            if item.type != "fact" or MANIFEST_FACT_PATTERN.match(item.statement)
        ]
        grouped = {
            memory_type: [item for item in candidates if item.type == memory_type]
            for memory_type in MANIFEST_TYPES
        }
        selected: list[ProjectionItem] = []
        for index in range(4):
            for memory_type in MANIFEST_TYPES:
                items = grouped[memory_type]
                if index >= len(items):
                    continue
                candidate = items[index]
                trial = [*selected, candidate]
                if self._manifest_tokens(trial, token_budget) <= token_budget:
                    selected.append(candidate)
        return selected

    def _manifest_tokens(self, items: list[ProjectionItem], token_budget: int) -> int:
        plan = ProjectionPlan(
            connector_id="budget",
            projection_id="projection_000000000000000000000000",
            items=items,
            mode="continuity_manifest",
            token_budget=token_budget,
        )
        return estimate_tokens(self._render_manifest(plan))

    @staticmethod
    def _render_manifest(plan: ProjectionPlan) -> str:
        lines = [
            "<!-- personalmemory:start -->",
            f"<!-- manifest_version: {MANIFEST_VERSION} -->",
            f"<!-- projection_id: {plan.projection_id} -->",
            f"<!-- token_budget: {plan.token_budget or 800} -->",
            "# PersonalMemory Continuity Manifest",
            "",
        ]
        for memory_type in MANIFEST_TYPES:
            items = [item for item in plan.items if item.type == memory_type]
            if not items:
                continue
            lines.extend([f"## {MANIFEST_TYPE_LABELS[memory_type]}", ""])
            lines.extend(f"- {item.statement}" for item in items)
            lines.append("")
        lines.extend(["## PersonalMemory Access", ""])
        lines.extend(f"- {line}" for line in MANIFEST_PROTOCOL_LINES)
        lines.extend(["", "<!-- personalmemory:end -->"])
        return "\n".join(lines)

    def project(self, connector_id: str) -> ProjectionResult:
        plan = self.plan(connector_id)
        rendered = self.render(plan)
        if plan.token_budget is not None and estimate_tokens(rendered) > plan.token_budget:
            raise ValueError("Projection exceeds configured token budget")

        with self.database.session_factory.begin() as session:
            connector = self._get_connector(session, connector_id)
            profile = get_builtin_profile(connector.profile_id)
            if len(rendered.encode(profile.encoding)) > profile.max_bytes:
                raise ValueError("Projection exceeds target profile capacity")
            target_path = Path(connector.target_path or "")
            existing_receipt = session.scalar(
                select(ProjectionReceiptRecord).where(
                    ProjectionReceiptRecord.projection_id == plan.projection_id
                )
            )
            if existing_receipt is not None and target_path.exists():
                current_hash = hash_bytes(target_path.read_bytes())
                if current_hash == existing_receipt.content_hash:
                    return ProjectionResult(
                        projection_id=plan.projection_id,
                        rendered_block=rendered,
                        file_hash=current_hash,
                        duplicate=True,
                    )

            writer = ManagedBlockWriter(
                start_marker=profile.managed_block.start_marker,
                end_marker=profile.managed_block.end_marker,
            )
            write_result = writer.write(
                target_path,
                rendered,
                expected_file_hash=connector.last_target_hash,
            )
            connector.last_target_hash = write_result.file_hash
            source_revision_ids = [f"{item.memory_id}:{item.revision}" for item in plan.items]
            session.add(
                ProjectionReceiptRecord(
                    id=new_id("receipt"),
                    connector_id=connector_id,
                    projection_id=plan.projection_id,
                    target_path=str(target_path.resolve()),
                    content_hash=write_result.file_hash,
                    source_revision_ids=source_revision_ids,
                )
            )
            session.add(
                LoopGuardRecord(
                    id=new_id("loop"),
                    connector_id=connector_id,
                    projection_id=plan.projection_id,
                    content_hash=write_result.file_hash,
                )
            )
            session.add(
                ActivityRecord(
                    id=new_id("activity"),
                    operation_id=new_id("operation"),
                    connector_id=connector_id,
                    kind="projection_updated",
                    status="completed",
                    summary=f"向 {connector.name} 更新 {len(plan.items)} 条记忆",
                    details={"projected": len(plan.items)},
                )
            )

        return ProjectionResult(
            projection_id=plan.projection_id,
            rendered_block=rendered,
            file_hash=write_result.file_hash,
            duplicate=False,
        )

    @staticmethod
    def _get_connector(session, connector_id: str) -> ConnectorInstanceRecord:
        connector = session.scalar(
            select(ConnectorInstanceRecord).where(ConnectorInstanceRecord.id == connector_id)
        )
        if connector is None:
            raise ValueError(f"Unknown connector: {connector_id}")
        return connector
