import type { MemoryItem, MemoryProvenance } from "@/api/client"
import { t } from "@/i18n"

function text(payload: Record<string, unknown>, keys: string[]): string | null {
  for (const key of keys) {
    const value = payload[key]
    if ((typeof value === "string" || typeof value === "number") && String(value).trim()) {
      return String(value).trim()
    }
  }
  return null
}

function integer(payload: Record<string, unknown>, keys: string[]): number | null {
  for (const key of keys) {
    const value = payload[key]
    if (typeof value === "number" && Number.isInteger(value)) return value
    if (typeof value === "string" && /^\d+$/.test(value)) return Number(value)
  }
  return null
}

export function provenanceFor(memory: MemoryItem): MemoryProvenance {
  if (memory.provenance) return memory.provenance
  const payload = memory.payload
  const authority = payload.semantic_authority
  const authorityRecord = authority && typeof authority === "object" && !Array.isArray(authority)
    ? authority as Record<string, unknown>
    : {}
  return {
    source_revision_id: text(payload, ["source_revision_id"]),
    source_kind: text(payload, ["source_kind"]),
    source_family: text(payload, ["source_family"]),
    source_format: text(payload, ["source_format"]),
    source_signal: text(payload, ["source_signal", "source"]),
    source_profile: text(payload, ["source_profile"]),
    capture_mode: text(payload, ["capture_mode"]),
    locator: text(payload, ["evidence_locator", "source_locator", "source_path", "path", "locator"]),
    project: text(payload, ["cwd", "project_root", "project"]),
    actor_kind: text(authorityRecord, ["actor_kind"]),
    actor_id: text(authorityRecord, ["actor_id"]),
    evidence_actor: text(payload, ["evidence_actor"]),
    evidence_excerpt: text(payload, ["evidence_excerpt"]),
    evidence_start: integer(payload, ["evidence_start", "start"]),
    evidence_end: integer(payload, ["evidence_end", "end"]),
    line_start: integer(payload, ["source_line_start", "line_start"]),
    line_end: integer(payload, ["source_line_end", "line_end"]),
    source_section: text(payload, ["source_section"]),
  }
}

export function evidenceLocation(provenance: MemoryProvenance): string {
  if (provenance.line_start !== null || provenance.line_end !== null) {
    const start = provenance.line_start ?? "?"
    const end = provenance.line_end ?? provenance.line_start ?? "?"
    return start === end ? t("第 {start} 行", { start }) : t("第 {start}-{end} 行", { start, end })
  }
  if (provenance.evidence_start !== null || provenance.evidence_end !== null) {
    return t("字符 {start}-{end}", {
      start: provenance.evidence_start ?? "?",
      end: provenance.evidence_end ?? "?",
    })
  }
  return t("未记录具体位置")
}

export function sourceName(provenance: MemoryProvenance): string {
  const source = provenance.source_family
    ?? provenance.source_profile
    ?? provenance.source_signal
    ?? provenance.source_format
  const labels: Record<string, string> = {
    claude_code: "Claude Code",
    codex: "Codex",
    standard_transcript: "标准对话记录",
    claude_memory_markdown: "Claude Agent 记忆",
    codex_memory_markdown: "Codex Agent 记忆",
    agent_memory: "Agent 原生记忆",
    agent_mcp: "Agent 主动提交",
    deterministic: "本地规则识别",
    llm: "可选模型提取",
    user_message: "用户明确表达",
    assistant_message: "Agent 回答或总结",
    markdown: "Markdown 文件",
    jsonl: "对话记录",
    sqlite: "本地数据库",
  }
  return source ? t(labels[source] ?? source) : t("未记录来源")
}

export function sourceFileName(locator: string | null): string | null {
  if (!locator) return null
  const normalized = locator.replace(/\\/g, "/").replace(/\/$/, "")
  return normalized.split("/").pop() || locator
}
