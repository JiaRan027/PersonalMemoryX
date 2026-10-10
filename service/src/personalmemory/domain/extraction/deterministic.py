"""DeterministicExtractorAdapter: zero-dependency reference extractor.

Design intent (v0.2): the first extraction path must be fully deterministic,
local, and independent of any model. Model-based extraction, when it exists,
goes through the same ExtractorPort and produces the same CandidateItems.

Rules are deliberately conservative: only user self-referential statements are
extracted, and any statement that looks like chit-chat or a question is skipped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from personalmemory.domain.extraction.extractor import (
    SOURCE_SIGNAL_DETERMINISTIC,
    CandidateItem,
    ExtractionResult,
)
from personalmemory.domain.memory.types import MemoryType

DEFAULT_IMPORTANCE = 0.5
GOAL_IMPORTANCE = 0.8
DECISION_IMPORTANCE = 0.8
INSIGHT_IMPORTANCE = 0.7
SKILL_IMPORTANCE = 0.6


@dataclass(frozen=True)
class _Rule:
    memory_type: MemoryType
    pattern: re.Pattern[str]
    confidence: float
    importance: float
    priority: int


def _first_group(match: re.Match[str]) -> str:
    return (match.group(1) or "").strip()


_RULES: list[_Rule] = [
    _Rule(
        MemoryType.GOAL,
        re.compile(
            r"^(?:我(?:的)?(?:目标|计划|打算|梦想|愿望)|I(?:'m| am)? (?:planning|going) to|"
            r"my goal is)",
            re.IGNORECASE,
        ),
        0.85,
        GOAL_IMPORTANCE,
        100,
    ),
    _Rule(
        MemoryType.DECISION,
        re.compile(
            r"^(?:我(?:最终)?(?:决定|选择|放弃|采用|使用|没有选择)(?:了|用|采用|使用|走)?|"
            r"I (?:decided|chose|picked|went with))",
            re.IGNORECASE,
        ),
        0.7,
        DECISION_IMPORTANCE,
        90,
    ),
    _Rule(
        MemoryType.PREFERENCE,
        re.compile(
            r"^(?:我(?:比较)?(?:喜欢|偏好|更愿意|习惯|倾向于|不喜欢|讨厌)|I (?:like|prefer|enjoy|"
            r"dislike|hate))",
            re.IGNORECASE,
        ),
        0.9,
        0.6,
        80,
    ),
    _Rule(
        MemoryType.SKILL,
        re.compile(
            # 精度优先：Agent 会话流里"我会+中文动作短语"几乎都是意图指令
            # （"我会先…/我会把…"），不是能力；只有"我会<具名技术>"（如"我会 Python"）
            # 才是能力表达，故要求"我会"后紧跟非中文字符。
            r"^(?:我会(?![一-鿿])|我能|我掌握|我熟悉|我用过|我精通|"
            r"I (?:can|know how to|am good at|have used|master))",
            re.IGNORECASE,
        ),
        0.7,
        SKILL_IMPORTANCE,
        70,
    ),
    _Rule(
        MemoryType.INSIGHT,
        re.compile(
            r"^(?:我(?:发现|意识到|总结|认识到|悟出)|I (?:realized|learned|noticed|concluded))",
            re.IGNORECASE,
        ),
        0.6,
        INSIGHT_IMPORTANCE,
        60,
    ),
    _Rule(
        MemoryType.EVENT,
        re.compile(
            r"^(?:我(?:完成|做过|参加|开发|上线|部署|踩过|迁移|搭建|写过)(?:了|过)?|"
            r"I (?:completed|built|shipped|deployed|migrated|attended))",
            re.IGNORECASE,
        ),
        0.75,
        DEFAULT_IMPORTANCE,
        50,
    ),
    _Rule(
        MemoryType.FACT,
        re.compile(
            r"^(?:我是|我住在|我在(?:一家|这家|那家)?|我的(?:职业|专业|身份|公司|团队|项目)|我使用|"
            r"我主要|"
            r"I (?:am|live in|work (?:as|at)|use))",
            re.IGNORECASE,
        ),
        0.9,
        DEFAULT_IMPORTANCE,
        40,
    ),
]

# Order by priority desc; first match wins.
_RULES_BY_PRIORITY = sorted(_RULES, key=lambda r: r.priority, reverse=True)

_NON_STATEMENT_PREFIX = re.compile(
    r"^(?:你|你们|请|帮我|为什么|如何|怎么|what|why|how|please|can you|do you)",
    re.IGNORECASE,
)
_ROLE_PREFIX = re.compile(
    r"^(用户|助手|系统|user|assistant|system|codex|claude|gemini|cursor)\s*[:：]\s*",
    re.IGNORECASE,
)
_USER_ROLE_NAMES = {"用户", "user"}
_REASON_SPLIT = re.compile(
    r"(?:因为|由于|理由是|原因是|所以|because|since|the reason is)"
    r"[:：]?\s*(.+)$"
)
_SENTENCE_SPLIT = re.compile(r"[。！？!?；;\n]+")


_REAL_ZH_RULES: list[_Rule] = [
    _Rule(
        MemoryType.GOAL,
        re.compile(
            r"^我(?:要|想|准备)(?=(?:开发|制作|创建|建设|打造|启动)(?:一个|一款|一套|新的?)?)"
        ),
        0.85,
        GOAL_IMPORTANCE,
        101,
    ),
    _Rule(
        MemoryType.GOAL,
        re.compile(r"^(?:我的?(?:目标|计划|打算|梦想|愿望)|我(?:计划|打算|希望|想要))"),
        0.85,
        GOAL_IMPORTANCE,
        100,
    ),
    _Rule(
        MemoryType.DECISION,
        re.compile(r"^我(?:最终)?(?:决定|选择|放弃|采用|使用|没有选择)"),
        0.7,
        DECISION_IMPORTANCE,
        90,
    ),
    _Rule(
        MemoryType.PREFERENCE,
        re.compile(r"^我(?:比较)?(?:喜欢|偏好|更愿意|习惯|倾向于|不喜欢|讨厌)"),
        0.9,
        0.6,
        80,
    ),
    _Rule(
        MemoryType.SKILL,
        re.compile(r"^(?:我会(?![一-龥])|我能|我掌握|我熟悉|我用过|我精通)"),
        0.7,
        SKILL_IMPORTANCE,
        70,
    ),
    _Rule(
        MemoryType.INSIGHT,
        re.compile(r"^我(?:发现|意识到|总结|认识到|悟出)"),
        0.6,
        INSIGHT_IMPORTANCE,
        60,
    ),
    _Rule(
        MemoryType.EVENT,
        re.compile(r"^我(?:完成|做过|参加|开发|上线|部署|踩过|迁移|搭建|写过)(?:了|过)?"),
        0.75,
        DEFAULT_IMPORTANCE,
        50,
    ),
    _Rule(
        MemoryType.FACT,
        re.compile(
            r"^(?:我是|我住在|我在(?:一家|这家|那家)?|我的(?:职业|专业|身份|公司|团队|项目)|我使用|我主要)"
        ),
        0.9,
        DEFAULT_IMPORTANCE,
        40,
    ),
]
_RULES_BY_PRIORITY = sorted([*_RULES, *_REAL_ZH_RULES], key=lambda r: r.priority, reverse=True)
_REAL_ZH_NON_STATEMENT_PREFIX = re.compile(r"^(?:你|你们|请|帮我|为什么|如何|怎么)", re.IGNORECASE)
_REAL_ZH_ROLE_PREFIX = re.compile(r"^(用户|助手|系统)\s*[:：]\s*", re.IGNORECASE)
_REAL_ZH_REASON_SPLIT = re.compile(r"(?:因为|由于|理由是|原因是|所以)[:：]?\s*(.+)$", re.IGNORECASE)
_ALL_SENTENCE_SPLIT = re.compile(rf"(?:{_SENTENCE_SPLIT.pattern}|[。！？；;]+)")
_QUESTION_SHAPED = re.compile(
    r"(?:\?|？|吗|么|什么|哪(?:儿|里)|为何|为什么|如何|怎么)",
    re.IGNORECASE,
)
_FENCED_BLOCK = re.compile(r"(?ms)(?:```|~~~).*?(?:```|~~~)")


class DeterministicExtractorAdapter:
    """Extracts self-referential memory statements from plain text."""

    def extract(
        self,
        content: str,
        *,
        source_revision_id: str,
        namespace: str,
    ) -> ExtractionResult:
        warnings: list[str] = []
        items: list[CandidateItem] = []

        if not content or not content.strip():
            return ExtractionResult(items=items, warnings=["empty content"])

        fenced_ranges = [match.span() for match in _FENCED_BLOCK.finditer(content)]
        cursor = 0
        active_role: str | None = None
        for sentence in _ALL_SENTENCE_SPLIT.split(content):
            sentence = sentence.strip()
            if not sentence:
                continue
            start = content.find(sentence, cursor)
            if start < 0:
                start = cursor
            end = start + len(sentence)
            cursor = end

            # A pasted example or code block is evidence about the source
            # conversation, not a user declaration. Keep offsets intact while
            # refusing the common "我是..." examples used in documentation.
            if any(
                start < range_end and end > range_start
                for range_start, range_end in fenced_ranges
            ):
                continue
            line_start = content.rfind("\n", 0, start) + 1
            if content[line_start:start].lstrip().startswith(">"):
                continue

            role: str | None = active_role
            match_sentence = sentence
            role_match = _REAL_ZH_ROLE_PREFIX.match(sentence) or _ROLE_PREFIX.match(sentence)
            if role_match:
                role = role_match.group(1)
                active_role = role
                match_sentence = sentence[role_match.end() :].strip()
            if role is not None and role.casefold() not in {*_USER_ROLE_NAMES, "用户"}:
                # Assistant/system statements are not user self-referential.
                continue

            if _NON_STATEMENT_PREFIX.match(match_sentence) or _REAL_ZH_NON_STATEMENT_PREFIX.match(
                match_sentence
            ):
                continue

            if _QUESTION_SHAPED.search(match_sentence):
                continue

            best = self._match_rule(match_sentence)
            if best is None:
                if len(match_sentence) >= 40 and (
                    "因为" in match_sentence or "所以" in match_sentence or "结论" in match_sentence
                ):
                    warnings.append(f"low_signal long sentence skipped: {match_sentence[:60]}")
                continue

            rule, match = best
            statement = self._normalize_statement(match_sentence, match)
            if not statement:
                continue
            reason = self._extract_reason(sentence)

            items.append(
                CandidateItem(
                    type=rule.memory_type,
                    statement=statement,
                    reason=reason,
                    confidence=round(rule.confidence, 2),
                    importance=round(rule.importance, 2),
                    evidence_span=(start, end),
                    source_signal=SOURCE_SIGNAL_DETERMINISTIC,
                    extra={"rule": "deterministic", "matched_prefix": match.group(0)},
                )
            )

        return ExtractionResult(items=items, warnings=warnings)

    @staticmethod
    def _match_rule(sentence: str):
        for rule in _RULES_BY_PRIORITY:
            match = rule.pattern.match(sentence)
            if match:
                return rule, match
        return None

    @staticmethod
    def _extract_reason(sentence: str) -> str | None:
        m = _REAL_ZH_REASON_SPLIT.search(sentence) or _REASON_SPLIT.search(sentence)
        if not m:
            return None
        reason = m.group(1).strip().rstrip("。.!！")
        return reason or None

    @staticmethod
    def _normalize_statement(sentence: str, match: re.Match[str]) -> str:
        statement = sentence[match.end() :].strip()
        statement = _REAL_ZH_REASON_SPLIT.sub("", statement)
        statement = _REASON_SPLIT.sub("", statement).strip(" .,;:：。；")
        return statement
