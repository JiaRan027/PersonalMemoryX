from __future__ import annotations

import json

import httpx
import pytest
from personalmemory.domain.extraction import (
    FallbackExtractor,
    LLMExtractionError,
    LLMExtractorAdapter,
    build_extractor,
)
from personalmemory.domain.memory.types import MemoryType
from personalmemory.settings import Settings


def make_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def json_response(items: list[dict]) -> str:
    return json.dumps(items, ensure_ascii=False)


def chat_handler(items: list[dict]):
    def handler(request: httpx.Request) -> httpx.Response:
        body = {"choices": [{"message": {"content": json_response(items)}}]}
        return httpx.Response(200, json=body)

    return handler


def test_llm_adapter_parses_candidates() -> None:
    client = make_client(
        chat_handler(
            [
                {
                    "type": "goal",
                    "statement": "我的目标是进入人工智能行业",
                    "reason": "长期兴趣",
                    "confidence": 0.9,
                    "importance": 0.8,
                },
                {
                    "type": "skill",
                    "statement": "我掌握 Python",
                    "reason": None,
                    "confidence": 0.95,
                    "importance": 0.7,
                },
            ]
        )
    )
    adapter = LLMExtractorAdapter(
        base_url="https://example.test/v1",
        api_key="secret-key",
        model="mock-model",
        client=client,
    )
    result = adapter.extract(
        "我最近开始学AI。我的目标是进入人工智能行业，我掌握 Python。",
        source_revision_id="s1",
        namespace="general",
    )
    assert len(result.items) == 2
    assert result.items[0].type is MemoryType.GOAL
    assert result.items[0].source_signal == "llm"
    assert result.items[0].evidence_span[0] >= 0
    assert result.items[1].type is MemoryType.SKILL
    assert result.items[0].confidence == 0.9
    assert result.warnings == []


def test_llm_adapter_truncates_to_tail_not_head() -> None:
    """Rollout 按时间追加，LLM 只能看到最新一段（尾部），而非最旧内容。"""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        user_content = body["messages"][-1]["content"]
        # 尾部保留、头部被截断
        assert "我的目标是进入人工智能行业。" in user_content
        assert "BEGIN_OLDEST_MARKER" not in user_content
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json_response(
                                [{"type": "goal", "statement": "我的目标是进入人工智能行业"}]
                            )
                        }
                    }
                ]
            },
        )

    adapter = LLMExtractorAdapter(
        base_url="https://example.test/v1",
        api_key="",
        model="m",
        max_chars=2_000,
        client=make_client(handler),
    )
    content = "BEGIN_OLDEST_MARKER " + "无意义填充" * 5_000 + " 我的目标是进入人工智能行业。"
    result = adapter.extract(content, source_revision_id="s1", namespace="g")
    assert len(result.items) == 1
    assert result.items[0].type is MemoryType.GOAL
    assert any("truncated to last" in warning for warning in result.warnings)


def test_llm_adapter_strips_markdown_fences() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json_response([{"type": "fact", "statement": "我住在上海"}])
        content = "```json\n" + payload + "\n```"
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    adapter = LLMExtractorAdapter(
        base_url="http://localhost:11434/v1",
        api_key="",
        model="qwen3",
        client=make_client(handler),
    )
    result = adapter.extract("我住在上海。", source_revision_id="s1", namespace="general")
    assert result.items[0].type is MemoryType.FACT


def test_llm_adapter_skips_invalid_items_with_warning() -> None:
    client = make_client(
        chat_handler(
            [
                {"type": "not_a_type", "statement": "x", "confidence": 0.9},
                {"type": "decision", "statement": "我决定采用结构化查询优先", "confidence": 0.8},
            ]
        )
    )
    adapter = LLMExtractorAdapter(
        base_url="https://example.test/v1",
        api_key="",
        model="m",
        client=client,
    )
    result = adapter.extract("我决定采用结构化查询优先", source_revision_id="s1", namespace="g")
    assert len(result.items) == 1
    assert any("skipped invalid" in warning for warning in result.warnings)


def test_llm_adapter_raises_on_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    adapter = LLMExtractorAdapter(
        base_url="https://example.test/v1",
        api_key="",
        model="m",
        client=make_client(handler),
    )
    with pytest.raises(LLMExtractionError):
        adapter.extract("我的目标是测试", source_revision_id="s1", namespace="g")


def test_llm_adapter_raises_on_malformed_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]})

    adapter = LLMExtractorAdapter(
        base_url="https://example.test/v1",
        api_key="",
        model="m",
        client=make_client(handler),
    )
    with pytest.raises(LLMExtractionError):
        adapter.extract("我的目标是测试", source_revision_id="s1", namespace="g")


def test_fallback_extractor_uses_llm_when_available() -> None:
    failing_llm = LLMExtractorAdapter(
        base_url="https://example.test/v1",
        api_key="",
        model="m",
        client=make_client(lambda r: httpx.Response(500)),
    )
    from personalmemory.domain.extraction.deterministic import DeterministicExtractorAdapter

    fallback = FallbackExtractor(primary=failing_llm, fallback=DeterministicExtractorAdapter())
    result = fallback.extract(
        "我的目标是进入人工智能行业。",
        source_revision_id="s1",
        namespace="g",
    )
    assert result.items
    assert any("fell back to deterministic" in warning for warning in result.warnings)


def test_build_extractor_deterministic_by_default() -> None:
    extractor = build_extractor(None)
    from personalmemory.domain.extraction.deterministic import DeterministicExtractorAdapter

    assert isinstance(extractor, DeterministicExtractorAdapter)


def test_build_extractor_returns_fallback_when_llm_enabled(tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path / "data",
        llm_enabled=True,
        llm_base_url="https://example.test/v1",
        llm_model="mock",
        llm_max_chars=77_000,
    )
    extractor = build_extractor(settings)
    assert isinstance(extractor, FallbackExtractor)
    assert extractor.primary.max_chars == 77_000


def test_build_extractor_ignores_incomplete_llm_config(tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path / "data",
        llm_enabled=True,
        llm_base_url="",
        llm_model="",
    )
    from personalmemory.domain.extraction.deterministic import DeterministicExtractorAdapter

    assert isinstance(build_extractor(settings), DeterministicExtractorAdapter)


def test_ingestion_drafts_delegate_to_build_extractor(monkeypatch) -> None:
    import personalmemory.application.ingestion as ingestion_module
    from personalmemory.application.ingestion import IngestionService
    from personalmemory.domain.connector.profile import get_builtin_profile
    from personalmemory.domain.extraction.extractor import ExtractionResult

    called: list[str] = []

    class FakeExtractor:
        def extract(self, content, *, source_revision_id, namespace):
            called.append("extracted")
            return ExtractionResult(items=[], warnings=[])

    monkeypatch.setattr(ingestion_module, "build_extractor", lambda settings: FakeExtractor())

    service = IngestionService(database=None, settings=object())
    profile = get_builtin_profile("conversation_text")
    drafts = service._drafts_from_extraction(
        profile,
        "我的目标是进入人工智能行业。",
        source_revision_id="s1",
        namespace="general",
    )
    assert called == ["extracted"]
    assert drafts == []
