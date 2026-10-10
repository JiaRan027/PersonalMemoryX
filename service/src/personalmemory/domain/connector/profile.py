from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from personalmemory.domain.memory.types import MemoryType


class ConnectorCapabilities(BaseModel):
    model_config = ConfigDict(frozen=True)

    read_memory_file: bool
    write_memory_file: bool
    mcp_context: bool = False
    observe_via_hook: bool = False
    # Existing hook connectors capture turn content unless explicitly disabled.
    capture_agent_turns: bool = True
    accept_envelopes: bool = False


class ManagedBlock(BaseModel):
    model_config = ConfigDict(frozen=True)

    start_marker: str = "<!-- personalmemory:start -->"
    end_marker: str = "<!-- personalmemory:end -->"
    format_version: str = "1"


class AgentMemoryProfile(BaseModel):
    model_config = ConfigDict(frozen=True)

    profile_id: str
    agent_family: str
    profile_version: str = "1.0.0"
    # Local sources intended to be followed for changes after the initial import.
    # Conversation exports and local chat databases stay snapshots by default;
    # an Agent's live integration should use Hook/MCP instead.
    watch_source_changes: bool = False
    capabilities: ConnectorCapabilities
    format: str = "markdown"
    encoding: str = "utf-8"
    max_bytes: int = 65_536
    managed_block: ManagedBlock = ManagedBlock()
    # 结构化 markdown 段落策略：normalized 段落名 -> 记忆类型；
    # None 表示显式跳过该段；skip_unmapped_sections 控制未映射段落的默认行为。
    section_types: dict[str, MemoryType | None] | None = None
    skip_unmapped_sections: bool = False
    capture_plain_lines: bool = False
    skip_frontmatter: bool = False
    skip_link_bullets: bool = False
    frontmatter_type_map: dict[str, MemoryType] | None = None
    projection_mode: Literal["full", "continuity_manifest"] = "full"
    projection_token_budget: int | None = None


BUILTIN_PROFILES = {
    "continuity_envelope_api": AgentMemoryProfile(
        profile_id="continuity_envelope_api",
        agent_family="generic_continuity_source",
        format="continuity_envelope",
        max_bytes=72 * 1024,
        capabilities=ConnectorCapabilities(
            read_memory_file=False,
            write_memory_file=False,
            accept_envelopes=True,
        ),
    ),
    "claude_code_hook": AgentMemoryProfile(
        profile_id="claude_code_hook",
        agent_family="claude_code",
        format="agent_hook",
        max_bytes=65_536,
        capabilities=ConnectorCapabilities(
            read_memory_file=False,
            write_memory_file=False,
            observe_via_hook=True,
            capture_agent_turns=True,
        ),
    ),
    "claude_code_global_context": AgentMemoryProfile(
        profile_id="claude_code_global_context",
        agent_family="claude_code",
        format="agent_hook",
        max_bytes=65_536,
        capabilities=ConnectorCapabilities(
            read_memory_file=False,
            write_memory_file=False,
            observe_via_hook=True,
            capture_agent_turns=False,
        ),
    ),
    "codex_global_context": AgentMemoryProfile(
        profile_id="codex_global_context",
        agent_family="codex",
        format="agent_hook",
        max_bytes=65_536,
        capabilities=ConnectorCapabilities(
            read_memory_file=False,
            write_memory_file=False,
            observe_via_hook=True,
            capture_agent_turns=False,
        ),
    ),
    "generic_markdown_readonly": AgentMemoryProfile(
        profile_id="generic_markdown_readonly",
        agent_family="generic_markdown_agent",
        watch_source_changes=True,
        capabilities=ConnectorCapabilities(
            read_memory_file=True,
            write_memory_file=False,
        ),
    ),
    "generic_markdown_managed": AgentMemoryProfile(
        profile_id="generic_markdown_managed",
        agent_family="generic_markdown_agent",
        watch_source_changes=True,
        capabilities=ConnectorCapabilities(
            read_memory_file=True,
            write_memory_file=True,
        ),
    ),
    "conversation_text": AgentMemoryProfile(
        profile_id="conversation_text",
        agent_family="conversation_text",
        format="conversation",
        max_bytes=262_144,
        capabilities=ConnectorCapabilities(
            read_memory_file=True,
            write_memory_file=False,
        ),
    ),
    "transcript_json": AgentMemoryProfile(
        profile_id="transcript_json",
        agent_family="standard_transcript",
        format="json",
        max_bytes=64 * 1024 * 1024,
        capabilities=ConnectorCapabilities(
            read_memory_file=True,
            write_memory_file=False,
        ),
    ),
    "transcript_jsonl": AgentMemoryProfile(
        profile_id="transcript_jsonl",
        agent_family="standard_transcript",
        format="jsonl",
        max_bytes=64 * 1024 * 1024,
        capabilities=ConnectorCapabilities(
            read_memory_file=True,
            write_memory_file=False,
        ),
    ),
    "codex_jsonl": AgentMemoryProfile(
        profile_id="codex_jsonl",
        agent_family="codex",
        format="jsonl",
        max_bytes=64 * 1024 * 1024,
        capabilities=ConnectorCapabilities(
            read_memory_file=True,
            write_memory_file=False,
        ),
    ),
    "claude_jsonl": AgentMemoryProfile(
        profile_id="claude_jsonl",
        agent_family="claude_code",
        format="jsonl",
        max_bytes=64 * 1024 * 1024,
        capabilities=ConnectorCapabilities(
            read_memory_file=True,
            write_memory_file=False,
        ),
    ),
    "gemini_sqlite": AgentMemoryProfile(
        profile_id="gemini_sqlite",
        agent_family="gemini_cli",
        format="sqlite",
        max_bytes=64 * 1024 * 1024,
        capabilities=ConnectorCapabilities(
            read_memory_file=True,
            write_memory_file=False,
        ),
    ),
    "cursor_sqlite": AgentMemoryProfile(
        profile_id="cursor_sqlite",
        agent_family="cursor",
        format="sqlite",
        max_bytes=16 * 1024 * 1024,
        capabilities=ConnectorCapabilities(
            read_memory_file=True,
            write_memory_file=False,
        ),
    ),
    "claude_memory_markdown": AgentMemoryProfile(
        profile_id="claude_memory_markdown",
        agent_family="claude_code",
        watch_source_changes=True,
        format="markdown",
        max_bytes=2 * 1024 * 1024,
        capabilities=ConnectorCapabilities(
            read_memory_file=True,
            write_memory_file=False,
        ),
        section_types={},
        skip_unmapped_sections=True,
        capture_plain_lines=True,
        skip_frontmatter=True,
        skip_link_bullets=True,
        frontmatter_type_map={"feedback": MemoryType.PREFERENCE},
    ),
    "codex_memory_markdown": AgentMemoryProfile(
        profile_id="codex_memory_markdown",
        agent_family="codex",
        watch_source_changes=True,
        format="markdown",
        max_bytes=2 * 1024 * 1024,
        capabilities=ConnectorCapabilities(
            read_memory_file=True,
            write_memory_file=False,
        ),
        # Raw Codex index/operations sections are not durable Core input. A
        # dedicated translator may later turn project-scoped workflow evidence
        # into governed candidates without making this importer do so silently.
        section_types={
            "userprofile": MemoryType.FACT,
            "userpreferences": MemoryType.PREFERENCE,
        },
        skip_unmapped_sections=True,
        capture_plain_lines=True,
    ),
    "personal_memory_projection_file": AgentMemoryProfile(
        profile_id="personal_memory_projection_file",
        agent_family="personal_memory_projection",
        capabilities=ConnectorCapabilities(
            read_memory_file=False,
            write_memory_file=True,
        ),
    ),
    "continuity_manifest_file": AgentMemoryProfile(
        profile_id="continuity_manifest_file",
        agent_family="personal_memory_projection",
        max_bytes=16_384,
        capabilities=ConnectorCapabilities(
            read_memory_file=False,
            write_memory_file=True,
        ),
        projection_mode="continuity_manifest",
        projection_token_budget=800,
    ),
}


def get_builtin_profile(profile_id: str) -> AgentMemoryProfile:
    try:
        return BUILTIN_PROFILES[profile_id].model_copy(deep=True)
    except KeyError as error:
        raise ValueError(f"Unknown profile: {profile_id}") from error
