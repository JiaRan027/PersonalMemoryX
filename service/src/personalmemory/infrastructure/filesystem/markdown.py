from __future__ import annotations

import re
from dataclasses import dataclass

from personalmemory.domain.connector.profile import AgentMemoryProfile
from personalmemory.domain.memory.model import MemoryDraft
from personalmemory.domain.memory.types import MemoryType


class SourceTooLargeError(ValueError):
    pass


SECTION_TYPES = {
    "fact": MemoryType.FACT,
    "facts": MemoryType.FACT,
    "userfact": MemoryType.FACT,
    "用户事实": MemoryType.FACT,
    "事实": MemoryType.FACT,
    "goal": MemoryType.GOAL,
    "goals": MemoryType.GOAL,
    "目标": MemoryType.GOAL,
    "skill": MemoryType.SKILL,
    "skills": MemoryType.SKILL,
    "技能": MemoryType.SKILL,
    "能力": MemoryType.SKILL,
    "preference": MemoryType.PREFERENCE,
    "preferences": MemoryType.PREFERENCE,
    "偏好": MemoryType.PREFERENCE,
    "event": MemoryType.EVENT,
    "events": MemoryType.EVENT,
    "事件": MemoryType.EVENT,
    "经历": MemoryType.EVENT,
    "insight": MemoryType.INSIGHT,
    "insights": MemoryType.INSIGHT,
    "洞察": MemoryType.INSIGHT,
    "认知": MemoryType.INSIGHT,
    "decision": MemoryType.DECISION,
    "decisions": MemoryType.DECISION,
    "决策": MemoryType.DECISION,
}

HEADING_PATTERN = re.compile(r"^(#{2,3})\s+(.+?)\s*$")
BULLET_PATTERN = re.compile(r"^\s*[-*+]\s+(.+?)\s*$")
CONTINUATION_PATTERN = re.compile(r"^\s{2,}(.+?)\s*$")
REASON_PATTERN = re.compile(
    r"^(?:\*\*)?(?:原因|理由|reason|why)(?:\*\*)?\s*[:：]\s*(.+)$",
    re.IGNORECASE,
)
REASON_APPEND_PATTERN = re.compile(
    r"^(?:desc|learnings?|why|原因|理由)\s*[:：]\s*(.+)$",
    re.IGNORECASE,
)
LINK_BULLET_PATTERN = re.compile(r"^\s*[-*+]\s+\[[^\]]+\]\([^)]*\)")
LOW_SIGNAL_PATTERN = re.compile(
    r"^(?:no additional .*(?:topics?|memories?)|暂无|无更多|无其他|n/a|none)[.!。]?$",
    re.IGNORECASE,
)


def normalize_section(value: str) -> str:
    return re.sub(r"[\s_'’_-]+", "", value).casefold()


@dataclass
class PendingItem:
    statement: str
    line_start: int
    line_end: int
    reason: str | None = None


class MarkdownMemoryParser:
    def __init__(self, profile: AgentMemoryProfile) -> None:
        self.profile = profile
        self.section_types = profile.section_types or SECTION_TYPES
        self.skip_unmapped = profile.skip_unmapped_sections

    def parse(
        self,
        content: str,
        *,
        source_revision_id: str,
        namespace: str,
    ) -> list[MemoryDraft]:
        if len(content.encode(self.profile.encoding)) > self.profile.max_bytes:
            raise SourceTooLargeError(
                f"Source exceeds {self.profile.max_bytes} byte profile limit"
            )

        drafts: list[MemoryDraft] = []
        current_section = "未分类"
        current_type = MemoryType.EVENT
        current_confidence = 0.5
        pending: PendingItem | None = None
        in_managed_block = False
        in_frontmatter = False
        frontmatter_armed = self.profile.skip_frontmatter
        frontmatter_type: str | None = None
        skip_section = self.skip_unmapped  # 未进入任何段落前，未映射来源默认跳过

        def flush() -> None:
            nonlocal pending
            if pending is None:
                return
            drafts.append(
                MemoryDraft(
                    type=current_type,
                    statement=pending.statement,
                    reason=pending.reason,
                    source_revision_id=source_revision_id,
                    confidence=current_confidence,
                    importance=0.5,
                    namespace=namespace,
                    payload={
                        "source_section": current_section,
                        "source_line_start": pending.line_start,
                        "source_line_end": pending.line_end,
                        **(
                            {"source_frontmatter_type": frontmatter_type}
                            if frontmatter_type
                            else {}
                        ),
                    },
                )
            )
            pending = None

        for line_number, line in enumerate(content.splitlines(), start=1):
            stripped = line.strip()
            if frontmatter_armed:
                # 只有首行是 --- 才进入 YAML frontmatter；否则该文件没有 frontmatter
                frontmatter_armed = False
                if stripped == "---":
                    in_frontmatter = True
                continue
            if in_frontmatter:
                if stripped == "---":
                    # YAML frontmatter 结束：按 type 决定正文处理方式
                    in_frontmatter = False
                    mapped = None
                    if frontmatter_type and self.profile.frontmatter_type_map:
                        mapped = self.profile.frontmatter_type_map.get(
                            frontmatter_type.strip().casefold()
                        )
                    if mapped is not None:
                        current_type = mapped
                        current_confidence = 0.9
                        skip_section = False
                    else:
                        skip_section = True
                elif frontmatter_type is None and stripped.casefold().startswith("type:"):
                    frontmatter_type = stripped.split(":", 1)[1].strip()
                continue
            if stripped == self.profile.managed_block.start_marker:
                flush()
                in_managed_block = True
                continue
            if stripped == self.profile.managed_block.end_marker:
                in_managed_block = False
                continue
            if in_managed_block:
                continue

            heading = HEADING_PATTERN.match(line)
            if heading:
                flush()
                current_section = heading.group(2).strip()
                normalized = normalize_section(current_section)
                mapped_type = self.section_types.get(normalized)
                if mapped_type is not None:
                    current_type = mapped_type
                    current_confidence = 0.9
                    skip_section = False
                elif heading.group(0).startswith("###"):
                    # 未映射的三级子标题（目录/日期等元数据）不切换段落类型
                    continue
                elif normalized in self.section_types or self.skip_unmapped:
                    # 显式跳过（值为 None）或 profile 要求忽略未映射段落
                    skip_section = True
                else:
                    current_type = MemoryType.EVENT
                    current_confidence = 0.5
                    skip_section = False
                continue

            if skip_section:
                # 跳过未映射/显式忽略段落中的条目（managed block 标记已在上面处理）
                continue

            if self.profile.skip_link_bullets and LINK_BULLET_PATTERN.match(line):
                # 记忆索引文件（- [标题](link) — 摘要）不产生记忆条目
                continue

            bullet = BULLET_PATTERN.match(line)
            if bullet:
                value = bullet.group(1).strip()
                if pending is not None:
                    reason_append = REASON_APPEND_PATTERN.match(value)
                    if reason_append:
                        pending.reason = (
                            f"{pending.reason}; " if pending.reason else ""
                        ) + reason_append.group(1).strip().strip("*").strip()
                        pending.line_end = line_number
                        continue
                flush()
                if LOW_SIGNAL_PATTERN.match(value):
                    # Codex 等来源的占位符（如 "No additional older high-signal topics."）不产生记忆
                    continue
                pending = PendingItem(
                    statement=value,
                    line_start=line_number,
                    line_end=line_number,
                )
                continue

            continuation = CONTINUATION_PATTERN.match(line)
            if pending is not None and continuation:
                value = continuation.group(1).strip()
                reason = REASON_PATTERN.match(value)
                if reason:
                    pending.reason = reason.group(1).strip().strip("*").strip()
                else:
                    pending.statement = f"{pending.statement} {value}"
                pending.line_end = line_number
                continue

            if not stripped:
                flush()
                continue

            if self.profile.capture_plain_lines:
                if stripped.casefold().startswith(("**how to apply", "how to apply")):
                    continue
                if stripped.startswith("#"):
                    # 标题状普通行：# 一级标题视为新任务块（回到默认跳过状态），
                    # 其余（#### 日期等）视为元数据，均不产生记忆条目。
                    flush()
                    if stripped.startswith("# ") or stripped == "#":
                        skip_section = self.skip_unmapped
                    continue
                if LOW_SIGNAL_PATTERN.match(stripped):
                    continue
                reason = REASON_PATTERN.match(stripped)
                if reason:
                    reason_text = reason.group(1).strip().strip("*").strip()
                    if pending is not None:
                        pending.reason = reason_text
                        pending.line_end = line_number
                    elif drafts:
                        # **Why:** 等标注可能紧跟空行出现，此时附加到上一记忆条目的 reason
                        drafts[-1].reason = reason_text
                    continue
                # Codex/Claude 记忆笔记常用普通段落而非 bullet
                flush()
                pending = PendingItem(
                    statement=stripped,
                    line_start=line_number,
                    line_end=line_number,
                )
                continue

        flush()
        return drafts
