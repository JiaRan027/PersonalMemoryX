from __future__ import annotations

import math
import re
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import select

from personalmemory.application.review_decisions import load_review_decisions
from personalmemory.application.review_triage import build_review_triage
from personalmemory.domain.continuity import DeliveryReceipt
from personalmemory.domain.memory.fingerprint import exact_content_fingerprint
from personalmemory.domain.memory.transfer import TransferProfile
from personalmemory.domain.memory.types import MemoryType, Sensitivity
from personalmemory.infrastructure.database.models import (
    ConnectorInstanceRecord,
    MemoryObjectRecord,
)
from personalmemory.infrastructure.database.search import MemorySearchIndex
from personalmemory.infrastructure.database.session import Database

DEFAULT_ALLOWED_TYPES = tuple(memory_type.value for memory_type in MemoryType)
SENSITIVITY_ORDER = {
    Sensitivity.PUBLIC.value: 0,
    Sensitivity.PERSONAL.value: 1,
    Sensitivity.SENSITIVE.value: 2,
    Sensitivity.SECRET.value: 3,
}
TYPE_WEIGHT = {
    MemoryType.DECISION.value: 8.5,
    MemoryType.GOAL.value: 8.0,
    MemoryType.INSIGHT.value: 7.5,
    MemoryType.PREFERENCE.value: 6.0,
    MemoryType.SKILL.value: 6.0,
    MemoryType.EVENT.value: 5.0,
    MemoryType.FACT.value: 5.0,
}
TYPE_INTENTS = {
    MemoryType.DECISION.value: (
        "决定",
        "决策",
        "选择",
        "取向",
        "架构",
        "方案",
        "路线",
        "采用",
        "默认",
        "为什么",
        "为何",
        "原因",
        "decision",
        "architecture",
        "why",
    ),
    MemoryType.INSIGHT.value: (
        "洞察",
        "认知",
        "经验",
        "教训",
        "核心",
        "理解",
        "为什么",
        "为何",
        "原因",
        "insight",
        "lesson",
        "why",
    ),
    MemoryType.PREFERENCE.value: (
        "偏好",
        "喜欢",
        "协作方式",
        "沟通方式",
        "preference",
        "prefer",
    ),
    MemoryType.GOAL.value: (
        "目标",
        "项目",
        "正在开发",
        "计划",
        "长期",
        "goal",
        "project",
        "plan",
    ),
    MemoryType.EVENT.value: (
        "以前",
        "过去",
        "发生",
        "经历",
        "失败",
        "暴露",
        "event",
        "history",
        "failed",
    ),
    MemoryType.SKILL.value: ("能力", "技能", "擅长", "skill", "ability"),
    MemoryType.FACT.value: ("身份", "背景", "事实", "是谁", "fact", "background"),
}
EN_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "do",
    "does",
    "for",
    "how",
    "i",
    "is",
    "it",
    "my",
    "of",
    "the",
    "to",
    "what",
    "which",
}

PREFLIGHT_TOKEN_BUDGET = 900
PREFLIGHT_ITEM_BUDGET = 760
PREFLIGHT_LIMIT = 6
PROFILE_BASELINE_TYPES = (
    MemoryType.FACT.value,
    MemoryType.GOAL.value,
    MemoryType.PREFERENCE.value,
    MemoryType.SKILL.value,
)
PROFILE_QUERY_PATTERNS = (
    re.compile(r"我(?:是|算是|像是)(?:一名|一个)?什么样的?人"),
    re.compile(r"你对我(?:有)?(?:哪些|那些|什么|多少)?(?:了解|印象|认识)"),
    re.compile(r"你(?:都)?(?:知道|了解|记得)(?:关于)?我(?:哪些|那些|什么|多少)(?:信息|情况|事情)?"),
    re.compile(r"(?:说说|介绍)(?:一下)?你对我的(?:了解|印象|认识)"),
    re.compile(r"(?:我是谁|个人画像|我的个人信息)"),
    re.compile(r"\b(?:what do you know about me|who am i|my profile|describe me)\b", re.I),
)


class PreflightContextError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def estimate_tokens(text: str) -> int:
    """Conservative dependency-free estimate for context budget enforcement."""
    cjk = len(re.findall(r"[\u3400-\u9fff]", text))
    non_cjk = len(text) - cjk
    return cjk + math.ceil(non_cjk / 4)


def lexical_tokens(text: str) -> set[str]:
    normalized = text.casefold()
    tokens = {
        token
        for token in re.findall(r"[a-z][a-z0-9_-]+", normalized)
        if token not in EN_STOPWORDS
    }
    for segment in re.findall(r"[\u3400-\u9fff]+", normalized):
        if len(segment) == 1:
            tokens.add(segment)
            continue
        for size in (2, 3, 4):
            for index in range(len(segment) - size + 1):
                tokens.add(segment[index : index + size])
    return tokens


def is_profile_query(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", text.strip().casefold())
    return any(pattern.search(normalized) for pattern in PROFILE_QUERY_PATTERNS)


class ContextCompiler:
    """Compile a bounded, explainable task context from authoritative memories."""

    candidate_pool_size = 200

    def __init__(self, database: Database, *, data_dir: Path | None = None) -> None:
        self.database = database
        self.data_dir = data_dir

    def compile_preflight(self, query: str, *, connector_id: str, cwd: str) -> dict[str, Any]:
        with self.database.session_factory() as session:
            connector = session.get(ConnectorInstanceRecord, connector_id)
            if connector is None:
                raise PreflightContextError("CONNECTOR_NOT_FOUND")
            if not connector.enabled or not connector.can_read or not connector.read_enabled:
                raise PreflightContextError("CONNECTOR_DISABLED")
            if not connector.source_path or not self._path_within(cwd, connector.source_path):
                raise PreflightContextError("PROJECT_SCOPE_DENIED")
            namespace = connector.namespace

        bundle = self.compile(
            query,
            namespaces=[namespace, "general"],
            max_sensitivity=Sensitivity.PERSONAL.value,
            token_budget=PREFLIGHT_ITEM_BUDGET,
            limit=PREFLIGHT_LIMIT,
            automatic=True,
            portable_transfers=True,
            max_transfer_items=2,
            baseline_types=PROFILE_BASELINE_TYPES if is_profile_query(query) else (),
            baseline_namespaces=("general",),
        )
        context = self.render_preflight(bundle["items"])
        while bundle["items"] and estimate_tokens(context) > PREFLIGHT_TOKEN_BUDGET:
            removed = bundle["items"].pop()
            bundle["estimated_tokens"] -= removed["estimated_tokens"]
            bundle["truncated"] = True
            context = self.render_preflight(bundle["items"])
        bundle["connector_id"] = connector_id
        bundle["context"] = context
        bundle["delivered_tokens"] = estimate_tokens(context)
        return bundle

    @staticmethod
    def _path_within(path_value: str, root_value: str) -> bool:
        path = Path(path_value).resolve(strict=False)
        root = Path(root_value).resolve(strict=False)
        return path == root or root in path.parents

    @staticmethod
    def render_preflight(items: list[dict[str, Any]]) -> str:
        if not items:
            return ""
        lines = [
            "Potentially relevant continuity context. Use it silently only when it helps "
            "the current task.",
            "A transfer item is an analogy, not a decision already made for the current "
            "project; explain important differences and limits.",
        ]
        for item in items:
            evidence = f"memory={item['id']} revision={item['revision']}"
            if item["match_kind"] == "transfer" and item.get("transfer"):
                transfer = item["transfer"]
                lines.append(
                    "- [transfer analogy] "
                    f"problem={transfer['problem_shape']}; principle={transfer['principle']}; "
                    f"applies_when={'; '.join(transfer['applies_when']) or 'unspecified'}; "
                    f"avoid_when={'; '.join(transfer['avoid_when']) or 'unspecified'}; "
                    f"source_statement={item['statement']}; {evidence}"
                )
            else:
                reason = f"; reason={item['reason']}" if item.get("reason") else ""
                lines.append(
                    f"- [direct {item['type']}] {item['statement']}{reason}; {evidence}"
                )
        return "\n".join(lines)

    def compile(
        self,
        query: str,
        *,
        requested_mode: Literal["lexical", "embedding", "hybrid"] = "lexical",
        namespaces: Sequence[str] | None = None,
        allowed_types: Sequence[str] = DEFAULT_ALLOWED_TYPES,
        max_sensitivity: str = Sensitivity.PERSONAL.value,
        token_budget: int = 1600,
        limit: int = 10,
        automatic: bool = False,
        portable_transfers: bool = False,
        max_transfer_items: int = 2,
        baseline_types: Sequence[str] = (),
        baseline_namespaces: Sequence[str] = (),
    ) -> dict[str, Any]:
        query = query.strip()
        namespace_values = tuple(dict.fromkeys(namespaces or ()))
        type_values = tuple(dict.fromkeys(allowed_types))
        sensitivity_limit = SENSITIVITY_ORDER[max_sensitivity]
        sensitivity_values = tuple(
            value
            for value, rank in SENSITIVITY_ORDER.items()
            if rank <= sensitivity_limit and value != Sensitivity.SECRET.value
        )
        direct_ids = set(
            MemorySearchIndex(self.database).search_ids(
                query, limit=self.candidate_pool_size
            )
        )
        predicates = [
            MemoryObjectRecord.status == "active",
            MemoryObjectRecord.type.in_(type_values),
            MemoryObjectRecord.sensitivity.in_(sensitivity_values),
        ]
        if namespace_values and not portable_transfers:
            predicates.append(MemoryObjectRecord.namespace.in_(namespace_values))
        with self.database.session_factory() as session:
            recent = session.scalars(
                select(MemoryObjectRecord)
                .where(*predicates)
                .order_by(MemoryObjectRecord.updated_at.desc())
                .limit(self.candidate_pool_size)
            ).all()
            direct = (
                session.scalars(
                    select(MemoryObjectRecord).where(
                        *predicates, MemoryObjectRecord.id.in_(direct_ids)
                    )
                ).all()
                if direct_ids
                else []
            )
            transfer_candidates = (
                session.scalars(
                    select(MemoryObjectRecord)
                    .where(
                        *predicates,
                        MemoryObjectRecord.payload["transfer"]["scope"].as_string()
                        == "cross_project",
                    )
                    .order_by(MemoryObjectRecord.updated_at.desc())
                    .limit(self.candidate_pool_size)
                ).all()
                if portable_transfers
                else []
            )

        candidates = {
            memory.id: memory
            for memory in [*direct, *recent, *transfer_candidates]
        }
        review_decisions = (
            load_review_decisions(self.data_dir) if self.data_dir is not None else {}
        )
        triage_by_memory = build_review_triage(
            candidates.values(), review_decisions=review_decisions
        )
        eligible_candidates = {
            memory_id: memory
            for memory_id, memory in candidates.items()
            if triage_by_memory[memory_id]["delivery_eligible"]
        }
        ranked = self._rank(
            query,
            eligible_candidates.values(),
            direct_ids,
            namespaces=set(namespace_values),
            automatic=automatic,
            portable_transfers=portable_transfers,
            baseline_types=set(baseline_types),
            baseline_namespaces=set(baseline_namespaces),
        )
        ranked, deduplicated_exact_count = self._deduplicate_exact_content(
            ranked, namespace_values
        )
        items: list[dict[str, Any]] = []
        estimated_tokens = 0
        truncated = False
        transfer_items = 0
        for memory, selection_reasons, match_kind, transfer in ranked:
            if len(items) >= limit:
                truncated = True
                break
            if match_kind == "transfer" and transfer_items >= max_transfer_items:
                truncated = True
                continue
            item = self._context_item(memory, selection_reasons, match_kind, transfer)
            item_tokens = estimate_tokens(self._budget_text(item))
            if estimated_tokens + item_tokens > token_budget:
                truncated = True
                continue
            item["estimated_tokens"] = item_tokens
            items.append(item)
            estimated_tokens += item_tokens
            if match_kind == "transfer":
                transfer_items += 1

        delivery_degraded = requested_mode != "lexical"
        return {
            "query": query,
            "namespaces": list(namespace_values),
            "allowed_types": list(type_values),
            "max_sensitivity": max_sensitivity,
            "token_budget": token_budget,
            "estimated_tokens": estimated_tokens,
            "truncated": truncated,
            "automatic": automatic,
            "items": items,
            "automatically_excluded_count": len(candidates) - len(eligible_candidates),
            "deduplicated_exact_count": deduplicated_exact_count,
            "delivery_receipt": DeliveryReceipt(
                requested_mode=requested_mode,
                effective_mode="lexical",
                status="degraded" if delivery_degraded else "ok",
                cache_class="dynamic",
                degradation_reason=(
                    "embedding_unavailable" if delivery_degraded else None
                ),
            ).model_dump(mode="json"),
        }

    @staticmethod
    def _deduplicate_exact_content(
        ranked: list[
            tuple[MemoryObjectRecord, list[str], str, TransferProfile | None]
        ],
        namespaces: Sequence[str],
    ) -> tuple[
        list[tuple[MemoryObjectRecord, list[str], str, TransferProfile | None]],
        int,
    ]:
        namespace_order = {namespace: index for index, namespace in enumerate(namespaces)}
        groups: dict[
            str, list[tuple[int, tuple[MemoryObjectRecord, list[str], str, TransferProfile | None]]]
        ] = {}
        for index, item in enumerate(ranked):
            memory = item[0]
            fingerprint = memory.exact_content_fingerprint or exact_content_fingerprint(
                memory.type, memory.statement
            )
            groups.setdefault(fingerprint, []).append((index, item))

        selected: list[
            tuple[int, tuple[MemoryObjectRecord, list[str], str, TransferProfile | None]]
        ] = []
        for matches in groups.values():
            best = min(
                matches,
                key=lambda entry: (
                    namespace_order.get(entry[1][0].namespace, len(namespace_order)),
                    entry[0],
                ),
            )
            selected.append(best)
        selected.sort(key=lambda entry: entry[0])
        result = [item for _, item in selected]
        return result, len(ranked) - len(result)

    def _rank(
        self,
        query: str,
        memories: Iterable[MemoryObjectRecord],
        direct_ids: set[str],
        *,
        namespaces: set[str],
        automatic: bool,
        portable_transfers: bool,
        baseline_types: set[str],
        baseline_namespaces: set[str],
    ) -> list[tuple[MemoryObjectRecord, list[str], str, TransferProfile | None]]:
        query_tokens = lexical_tokens(query)
        normalized_query = query.casefold()
        scored: list[
            tuple[float, MemoryObjectRecord, list[str], str, TransferProfile | None]
        ] = []
        for memory in memories:
            transfer = self._transfer_profile(memory)
            in_scope = not namespaces or memory.namespace in namespaces
            portable = bool(
                portable_transfers
                and not in_scope
                and transfer is not None
                and transfer.scope == "cross_project"
            )
            if not in_scope and not portable:
                continue
            memory_tokens = lexical_tokens(
                f"{memory.statement} {memory.reason or ''} {memory.namespace}"
            )
            overlap = query_tokens & memory_tokens
            transfer_overlap = (
                query_tokens & lexical_tokens(transfer.retrieval_text())
                if transfer is not None
                else set()
            )
            reasons: list[str] = []
            score = TYPE_WEIGHT.get(memory.type, 0.0)
            profile_baseline = bool(
                memory.type in baseline_types
                and in_scope
                and (not baseline_namespaces or memory.namespace in baseline_namespaces)
            )
            if profile_baseline:
                score += 12.0
                reasons.append("profile_baseline")
            if memory.id in direct_ids and in_scope:
                score += 50.0
                reasons.append("exact_and_match")
            if overlap:
                score += min(len(overlap), 12) * 4.0
                reasons.append("lexical_overlap")
            if portable and len(transfer_overlap) >= 2:
                score += min(len(transfer_overlap), 12) * 5.0
                reasons.append("transfer_overlap")
            intents = TYPE_INTENTS.get(memory.type, ())
            if not automatic and any(intent in normalized_query for intent in intents):
                score += 20.0
                reasons.append(f"type_intent:{memory.type}")
            if portable and "transfer_overlap" not in reasons:
                continue
            if automatic and not {
                "exact_and_match",
                "lexical_overlap",
                "transfer_overlap",
                "profile_baseline",
            }.intersection(reasons):
                continue
            if not reasons:
                continue
            if memory.epistemic_state == "inferred":
                score -= 5.0
                reasons.append("provisional")
            scored.append((score, memory, reasons, "transfer" if portable else "direct", transfer))
        scored.sort(
            key=lambda item: (item[0], item[1].updated_at, item[1].id), reverse=True
        )
        return [
            (memory, reasons, match_kind, transfer)
            for _, memory, reasons, match_kind, transfer in scored
        ]

    @staticmethod
    def _transfer_profile(memory: MemoryObjectRecord) -> TransferProfile | None:
        value = (memory.payload or {}).get("transfer")
        if not isinstance(value, dict):
            return None
        try:
            return TransferProfile.model_validate(value)
        except ValueError:
            return None

    @staticmethod
    def _context_item(
        memory: MemoryObjectRecord,
        selection_reasons: list[str],
        match_kind: str,
        transfer: TransferProfile | None,
    ) -> dict[str, Any]:
        payload = memory.payload or {}
        provenance = {
            "source": payload.get("source")
            or payload.get("source_signal")
            or payload.get("source_format")
            or "unknown",
        }
        for key in (
            "source_format",
            "source_signal",
            "evidence_start",
            "evidence_end",
        ):
            if key in payload:
                provenance[key] = payload[key]
        return {
            "id": memory.id,
            "type": memory.type,
            "statement": memory.statement,
            "reason": memory.reason,
            "namespace": memory.namespace,
            "sensitivity": memory.sensitivity,
            "epistemic_state": memory.epistemic_state,
            "revision": memory.revision,
            "updated_at": memory.updated_at.isoformat(),
            "selection_reasons": selection_reasons,
            "match_kind": match_kind,
            "transfer": transfer.model_dump(mode="json") if transfer is not None else None,
            "provenance": provenance,
        }

    @staticmethod
    def _budget_text(item: dict[str, Any]) -> str:
        reason = f" reason={item['reason']}" if item.get("reason") else ""
        source = item["provenance"]["source"]
        transfer = item.get("transfer")
        transfer_text = (
            " transfer="
            + " ".join(
                [
                    transfer["problem_shape"],
                    transfer["principle"],
                    *transfer["applies_when"],
                    *transfer["avoid_when"],
                ]
            )
            if transfer
            else ""
        )
        return (
            f"[{item['type']}] {item['statement']}{reason} "
            f"namespace={item['namespace']} revision={item['revision']} source={source}"
            f" match={item['match_kind']}{transfer_text}"
        )
