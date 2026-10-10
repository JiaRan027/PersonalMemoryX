import { describe, expect, it } from "vitest"

import type { MemoryItem } from "@/api/client"
import { evidenceLocation, provenanceFor, sourceFileName, sourceName } from "@/memories/provenance"

function memory(payload: Record<string, unknown>): MemoryItem {
  return {
    id: "memory_1",
    type: "decision",
    statement: "Use dynamic token discovery.",
    reason: "Tokens rotate.",
    namespace: "projects/personalmemory",
    sensitivity: "personal",
    epistemic_state: "asserted",
    status: "active",
    revision: 1,
    payload,
    created_at: "2026-08-05T00:00:00Z",
    updated_at: "2026-08-05T00:00:00Z",
  }
}

describe("memory provenance presentation", () => {
  it("derives provenance from older Service payloads", () => {
    const result = provenanceFor(memory({
      source_family: "claude_code",
      source_locator: "C:\\work\\PersonalMemory\\MEMORY.md",
      source_line_start: 12,
      source_line_end: 14,
      evidence_excerpt: "User decided to keep the Core local.",
      semantic_authority: { actor_kind: "user", actor_id: "local-user" },
    }))

    expect(sourceName(result)).toBe("Claude Code")
    expect(sourceFileName(result.locator)).toBe("MEMORY.md")
    expect(evidenceLocation(result)).toBe("第 12-14 行")
    expect(result.actor_kind).toBe("user")
  })

  it("prefers the normalized API provenance view", () => {
    const item = memory({ source_family: "codex" })
    item.provenance = {
      source_revision_id: "source_1",
      source_kind: "interaction",
      source_family: "standard_transcript",
      source_format: "jsonl",
      source_signal: "user_message",
      source_profile: "transcript_jsonl",
      capture_mode: "offline_import",
      locator: "session://one/message/24",
      project: null,
      actor_kind: "user",
      actor_id: null,
      evidence_actor: "user",
      evidence_excerpt: "I decided to use SQLite.",
      evidence_start: 10,
      evidence_end: 34,
      line_start: null,
      line_end: null,
      source_section: null,
    }

    const result = provenanceFor(item)
    expect(sourceName(result)).toBe("标准对话记录")
    expect(evidenceLocation(result)).toBe("字符 10-34")
    expect(result.source_revision_id).toBe("source_1")
  })
})
