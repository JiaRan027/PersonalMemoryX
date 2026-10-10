from __future__ import annotations

import json
import re
from typing import Any

import httpx

from personalmemory.domain.extraction.extractor import (
    SOURCE_SIGNAL_LLM,
    CandidateItem,
    ExtractionResult,
    ExtractorPort,
)
from personalmemory.domain.memory.types import MemoryType

MAX_SOURCE_CHARS = 120_000

SYSTEM_PROMPT = (
    "你是 PersonalMemory 的记忆抽取器。"
    "从用户提供的文本中抽取“长期有效”的个人记忆候选。"
    "只输出 JSON，不要输出任何其他文字。\n\n"
    "记忆类型（type 只能是以下之一）：\n"
    "- fact：用户事实（背景、身份、基础信息）\n"
    "- goal：用户目标（计划、愿望）\n"
    "- skill：用户能力（掌握的工具/技能，需有证据）\n"
    "- preference：交互或内容偏好\n"
    "- event：用户经历的事件（项目、学习、尝试）\n"
    "- insight：用户形成的认知/结论（含原因）\n"
    "- decision：用户做过的决策（含方案与原因）\n\n"
    "规则：\n"
    "1. 只抽取文本中明确出现的内容，不要编造、不要推测。\n"
    "2. statement 用第一人称陈述句，去掉“我决定”“我的目标是”等前缀，\n"
    "   保留核心断言。\n"
    "3. reason 记录形成该记忆的原因，可为 null。\n"
    "4. confidence：对“文本确实表达了该记忆”的把握，取值 0-1。\n"
    "5. importance：该记忆对长期理解用户的价值，取值 0-1。\n"
    "6. 忽略寒暄、无信息量内容、临时性操作指令。\n"
    "7. 输出严格 JSON 数组：\n"
    '   [{"type": "goal", "statement": "...", "reason": "...",\n'
    '    "confidence": 0.8, "importance": 0.7}]'
)


class LLMExtractionError(RuntimeError):
    """Raised when the LLM call fails or returns unusable output."""


class LLMExtractorAdapter:
    """Optional OpenAI-compatible extractor. Only produces candidates."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 30.0,
        max_chars: int = MAX_SOURCE_CHARS,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.max_chars = max(1_000, max_chars)
        self._client = client or httpx.Client(timeout=timeout_seconds)

    def extract(
        self,
        content: str,
        *,
        source_revision_id: str,
        namespace: str,
    ) -> ExtractionResult:
        if not content or not content.strip():
            return ExtractionResult(items=[], warnings=["empty content"])

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        warnings: list[str] = []
        user_content = content
        if len(content) > self.max_chars:
            # Rollouts 按时间追加，最新经验在尾部；LLM 只提炼最近一段。
            user_content = content[-self.max_chars:]
            warnings.append(
                f"content truncated to last {self.max_chars} chars"
            )
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        try:
            response = self._client.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=body,
            )
            response.raise_for_status()
            data = response.json()
            text = data["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as error:
            raise LLMExtractionError(f"llm request failed: {error}") from error

        items, parse_warnings = self._parse_items(text, content)
        warnings.extend(parse_warnings)
        return ExtractionResult(items=items, warnings=warnings)

    def _parse_items(
        self, raw: str, content: str
    ) -> tuple[list[CandidateItem], list[str]]:
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as error:
            raise LLMExtractionError(f"llm returned invalid json: {error}") from error
        if not isinstance(data, list):
            raise LLMExtractionError("llm returned non-list json")

        valid_types = {member.value for member in MemoryType}
        items: list[CandidateItem] = []
        warnings: list[str] = []
        for index, entry in enumerate(data):
            if not isinstance(entry, dict):
                continue
            raw_type = str(entry.get("type", "")).strip().casefold()
            statement = str(entry.get("statement", "")).strip()
            if raw_type not in valid_types or not statement:
                warnings.append(f"item {index}: skipped invalid type/statement")
                continue
            try:
                confidence = self._clamp(float(entry.get("confidence", 0.5)))
                importance = self._clamp(float(entry.get("importance", 0.5)))
            except (TypeError, ValueError):
                confidence, importance = 0.5, 0.5
            reason = entry.get("reason")
            reason = str(reason).strip() if reason else None

            start = content.casefold().find(statement.casefold())
            span = (start, start + len(statement)) if start >= 0 else (0, 0)
            items.append(
                CandidateItem(
                    type=MemoryType(raw_type),
                    statement=statement,
                    reason=reason,
                    confidence=round(confidence, 2),
                    importance=round(importance, 2),
                    evidence_span=span,
                    source_signal=SOURCE_SIGNAL_LLM,
                    extra={"rule": "llm"},
                )
            )
        if not items:
            warnings.append("llm returned no usable candidates")
        return items, warnings

    @staticmethod
    def _clamp(value: float) -> float:
        return max(0.0, min(1.0, value))


class FallbackExtractor:
    """Tries the primary extractor and falls back to a deterministic one."""

    def __init__(self, primary: ExtractorPort, fallback: ExtractorPort) -> None:
        self.primary = primary
        self.fallback = fallback

    def extract(
        self,
        content: str,
        *,
        source_revision_id: str,
        namespace: str,
    ) -> ExtractionResult:
        try:
            return self.primary.extract(
                content,
                source_revision_id=source_revision_id,
                namespace=namespace,
            )
        except LLMExtractionError as error:
            result = self.fallback.extract(
                content,
                source_revision_id=source_revision_id,
                namespace=namespace,
            )
            result.warnings.insert(0, f"llm unavailable, fell back to deterministic: {error}")
            return result


def build_extractor(settings: Any) -> ExtractorPort:
    """Deterministic by default; opt-in LLM with automatic fallback."""
    from personalmemory.domain.extraction.deterministic import (
        DeterministicExtractorAdapter,
    )

    deterministic: ExtractorPort = DeterministicExtractorAdapter()
    if settings is None or not settings.llm_enabled:
        return deterministic
    if not settings.llm_base_url or not settings.llm_model:
        return deterministic
    llm = LLMExtractorAdapter(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        timeout_seconds=settings.llm_timeout_seconds,
        max_chars=settings.llm_max_chars,
    )
    return FallbackExtractor(primary=llm, fallback=deterministic)
