import { describe, expect, it } from "vitest"

import type { Connector } from "@/api/client"
import { connectorSourceRole, isContinuousConnector, isImportedSnapshot } from "@/connectors/presentation"

function connector(profile_id: string, capabilities = { read: true, write: false }): Connector {
  return {
    id: "connector_test",
    name: "本地来源",
    profile_id,
    source_path: "C:/Users/demo/.codex/sessions/2026/07/session.jsonl",
    target_path: null,
    namespace: "general",
    capabilities,
    read_enabled: true,
    write_enabled: false,
    enabled: true,
    status: "healthy",
    background_sync: false,
  }
}

describe("connector presentation", () => {
  it("presents archived Codex transcripts as imports instead of live connections", () => {
    const item = connector("codex_jsonl")

    expect(isContinuousConnector(item)).toBe(false)
    expect(isImportedSnapshot(item)).toBe(true)
    expect(connectorSourceRole(item).label).toBe("历史对话文件")
    expect(connectorSourceRole(item).warning).toContain("不是实时 Agent 接入")
  })

  it("keeps explicit live integrations and managed projections continuous", () => {
    expect(isContinuousConnector(connector("codex_global_context"))).toBe(true)
    expect(isContinuousConnector(connector("generic_markdown_managed", { read: true, write: true }))).toBe(true)
  })
})
