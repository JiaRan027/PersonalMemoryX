import { describe, expect, it } from "vitest"

import type { MemoryItem } from "@/api/client"
import { needsMemoryReview } from "@/memories/presentation"

function memory(overrides: Partial<MemoryItem> = {}): MemoryItem {
  return {
    id: "memory_1",
    type: "decision",
    statement: "Continuity Core remains platform independent.",
    reason: "Agents are replaceable while continuity belongs to the user.",
    namespace: "projects/personalmemory",
    sensitivity: "personal",
    epistemic_state: "asserted",
    status: "active",
    revision: 1,
    payload: {},
    quality_issues: [],
    created_at: "2026-08-05T00:00:00Z",
    updated_at: "2026-08-05T00:00:00Z",
    ...overrides,
  }
}

describe("memory review presentation", () => {
  it("keeps confirmed continuity visible by default", () => {
    expect(needsMemoryReview(memory())).toBe(false)
  })

  it("uses automatic triage instead of sending every inferred record to the user", () => {
    expect(needsMemoryReview(memory({
      epistemic_state: "inferred",
      review_triage: {
        suggested_verdict: "source_only", effective_verdict: "source_only", confidence: "high",
        reason_codes: ["AGENT_WORKFLOW_NOTE"], requires_user_review: false,
        delivery_eligible: false, graph_eligible: false, scope_hint: null,
        overridden_by_user: false, memory_revision: 1,
      },
    }))).toBe(false)
    expect(needsMemoryReview(memory({
      epistemic_state: "inferred",
      review_triage: {
        suggested_verdict: "needs_context", effective_verdict: "needs_context", confidence: "medium",
        reason_codes: ["UNVERIFIED_AGENT_HIGH_IMPACT"], requires_user_review: true,
        delivery_eligible: false, graph_eligible: false, scope_hint: null,
        overridden_by_user: false, memory_revision: 1,
      },
    }))).toBe(true)
  })
})
