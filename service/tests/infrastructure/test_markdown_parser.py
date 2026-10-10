from pathlib import Path

FIXTURE = Path(__file__).parents[1] / "fixtures" / "source-memory.md"


def test_builtin_profiles_keep_read_and_write_capabilities_independent():
    from personalmemory.domain.connector.profile import get_builtin_profile

    readonly = get_builtin_profile("generic_markdown_readonly")
    managed = get_builtin_profile("generic_markdown_managed")
    projection = get_builtin_profile("personal_memory_projection_file")
    manifest = get_builtin_profile("continuity_manifest_file")

    assert readonly.capabilities.read_memory_file is True
    assert readonly.capabilities.write_memory_file is False
    assert managed.capabilities.read_memory_file is True
    assert managed.capabilities.write_memory_file is True
    assert projection.capabilities.read_memory_file is False
    assert projection.capabilities.write_memory_file is True
    assert manifest.capabilities.read_memory_file is False
    assert manifest.capabilities.write_memory_file is True
    assert manifest.projection_mode == "continuity_manifest"
    assert manifest.projection_token_budget == 800
    assert managed.managed_block.start_marker == "<!-- personalmemory:start -->"


def test_parser_maps_sections_and_excludes_managed_projection():
    from personalmemory.domain.connector.profile import get_builtin_profile
    from personalmemory.infrastructure.filesystem.markdown import MarkdownMemoryParser

    parser = MarkdownMemoryParser(get_builtin_profile("generic_markdown_managed"))
    drafts = parser.parse(
        FIXTURE.read_text(encoding="utf-8"),
        source_revision_id="source_01",
        namespace="projects/personalmemory",
    )

    assert [draft.type.value for draft in drafts] == [
        "fact",
        "goal",
        "insight",
        "decision",
        "event",
        "fact",
    ]
    assert all("不应重新摄入" not in draft.statement for draft in drafts)
    assert drafts[2].reason == "模型无法可靠预测未来价值。"
    assert drafts[3].reason == "大量个人记忆属于明确事实。"
    assert drafts[4].confidence == 0.5
    assert drafts[0].namespace == "projects/personalmemory"


def test_codex_memory_markdown_maps_real_memory_sections():
    """Codex 真实记忆文件：User Profile->fact、User preferences->preference、
    General Tips/Reusable knowledge/Failures->insight，Task 组元数据跳过。"""
    from personalmemory.domain.connector.profile import get_builtin_profile
    from personalmemory.infrastructure.filesystem.markdown import MarkdownMemoryParser

    content = """# Task Group: ExampleProject replies

scope: debug reply nesting
applies_to: cwd=somewhere

## User Profile

The user works on ExampleProject across uniapp, backend, and Android.

## User preferences

- When implementing a feature, update the project development log in the same change.
- Do not report browser or UI work complete until verified.

## General Tips

- Keep verification status precise.
- Never claim a build passed without evidence.

## Reusable knowledge

- Send `parentId`, not `replyToUserId`.

## Failures and how to do differently

- Symptom: WeChat reports module not defined.

## Task 1: Uniapp comment replies

### keywords

- ExampleProject, CommentSection, parentId

- rollout_summaries/2026-07-18-something.md
"""

    parser = MarkdownMemoryParser(get_builtin_profile("codex_memory_markdown"))
    drafts = parser.parse(
        content, source_revision_id="source_01", namespace="codex_memories"
    )
    types = [draft.type.value for draft in drafts]
    assert types == ["fact", "preference", "preference"], types
    assert "ExampleProject across uniapp" in drafts[0].statement
    assert "update the project development log" in drafts[1].statement
    assert all("Send `parentId`" not in d.statement for d in drafts)
    assert all("Symptom: WeChat reports module not defined" not in d.statement for d in drafts)
    assert "ExampleProject, CommentSection" not in " ".join(d.statement for d in drafts)
    assert all(draft.namespace == "codex_memories" for draft in drafts)
    assert drafts[0].payload["source_section"] == "User Profile"


def test_codex_memory_markdown_parses_whats_in_memory_and_reasons():
    """真实 memory_summary.md：What's in Memory 的目录/日期为元数据，
    desc:/learnings: 子条目作为 reason 附加到事件。"""
    from personalmemory.domain.connector.profile import get_builtin_profile
    from personalmemory.infrastructure.filesystem.markdown import MarkdownMemoryParser

    content = r"""# Task Group: ExampleProject replies

## What's in Memory

### C:\Users\test-user\Documents\ExampleProject

#### 2026-07-25

- ExampleProject two-level comment replies: CommentSection.vue, parentId
  - desc: Cross-client reply contract and uniapp UI implementation.
  - learnings: Node suite reported 7 passed, 0 failed.

### Older Memory Topics

- No additional older high-signal topics.
"""
    parser = MarkdownMemoryParser(get_builtin_profile("codex_memory_markdown"))
    drafts = parser.parse(
        content, source_revision_id="source_01", namespace="codex_memories"
    )
    assert drafts == []
    # 占位符（No additional ... topics）不应成为记忆
    assert all("additional older high-signal" not in d.statement for d in drafts)


def test_claude_memory_markdown_feedback_and_index():
    """Claude 笔记：frontmatter feedback -> preference，**Why:** 作 reason，索引跳过。"""
    from personalmemory.domain.connector.profile import get_builtin_profile
    from personalmemory.infrastructure.filesystem.markdown import MarkdownMemoryParser

    feedback = """---
name: 始终用中文回答
description: 用户要求所有回答都使用中文
type: feedback
originSessionId: abc
---
始终用中文回答用户的问题。

**Why:** 用户明确要求使用中文交流。
**How to apply:** 所有回复、解释、总结都使用中文，代码和技术术语可保留英文。
"""

    parser = MarkdownMemoryParser(get_builtin_profile("claude_memory_markdown"))
    drafts = parser.parse(
        feedback, source_revision_id="source_01", namespace="claude_memory"
    )
    assert len(drafts) == 1, [d.statement for d in drafts]
    assert drafts[0].type.value == "preference"
    assert "始终用中文回答用户的问题" in drafts[0].statement
    assert "How to apply" not in drafts[0].statement
    assert drafts[0].reason == "用户明确要求使用中文交流。"

    # 链接索引文件不产生记忆
    index = "- [始终用中文回答](feedback_language.md) — 用户要求所有回复使用中文\n"
    drafts_index = parser.parse(
        index, source_revision_id="source_02", namespace="claude_memory"
    )
    assert drafts_index == []


def test_parser_preserves_source_line_boundaries():
    from personalmemory.domain.connector.profile import get_builtin_profile
    from personalmemory.infrastructure.filesystem.markdown import MarkdownMemoryParser

    drafts = MarkdownMemoryParser(get_builtin_profile("generic_markdown_readonly")).parse(
        FIXTURE.read_text(encoding="utf-8"),
        source_revision_id="source_01",
        namespace="general",
    )

    first = drafts[0]
    assert first.payload["source_section"] == "用户事实"
    assert first.payload["source_line_start"] == 5
    assert first.payload["source_line_end"] == 5
    assert drafts[2].payload["source_line_end"] == 14


def test_parser_rejects_source_larger_than_profile_limit():
    import pytest
    from personalmemory.domain.connector.profile import get_builtin_profile
    from personalmemory.infrastructure.filesystem.markdown import (
        MarkdownMemoryParser,
        SourceTooLargeError,
    )

    profile = get_builtin_profile("generic_markdown_readonly").model_copy(
        update={"max_bytes": 8}
    )

    with pytest.raises(SourceTooLargeError):
        MarkdownMemoryParser(profile).parse(
            "## Fact\n- too large",
            source_revision_id="source_01",
            namespace="general",
        )
