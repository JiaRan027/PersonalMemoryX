import type { Connector } from "@/api/client"
import { t } from "@/i18n"

const PROFILE_LABELS: Record<string, string> = {
  claude_code_global_context: "Claude Code 个人连续性",
  claude_code_hook: "Claude Code 项目连续性",
  continuity_envelope_api: "通用 Agent API",
  continuity_manifest_file: "轻量上下文投影",
  personal_memory_projection_file: "受管记忆投影",
  generic_markdown_managed: "双向 Markdown",
  generic_markdown_readonly: "Markdown 文件",
  claude_memory_markdown: "Claude 记忆文件",
  codex_memory_markdown: "Codex 记忆文件",
  claude_jsonl: "Claude 对话记录",
  codex_jsonl: "Codex 对话记录",
  conversation_text: "对话文本",
  transcript_json: "标准对话记录",
  transcript_jsonl: "标准对话记录",
  cursor_sqlite: "Cursor 本地记录",
  gemini_sqlite: "Gemini 本地记录",
}

const IMPORTED_SOURCE_PROFILES = new Set([
  "generic_markdown_readonly",
  "claude_memory_markdown",
  "codex_memory_markdown",
  "claude_jsonl",
  "codex_jsonl",
  "conversation_text",
  "transcript_json",
  "transcript_jsonl",
  "cursor_sqlite",
  "gemini_sqlite",
])

const BACKGROUND_SYNC_PROFILES = new Set([
  "generic_markdown_readonly",
  "generic_markdown_managed",
  "claude_memory_markdown",
  "codex_memory_markdown",
])

export function watchesSourceChanges(connector: Connector): boolean {
  return connector.background_sync ?? BACKGROUND_SYNC_PROFILES.has(connector.profile_id)
}

export function connectorProfileLabel(profileId: string): string {
  return t(PROFILE_LABELS[profileId] ?? "自定义本地来源")
}

export function isContinuousConnector(connector: Connector): boolean {
  if (isImportedSnapshot(connector)) return false
  return watchesSourceChanges(connector)
    || connector.profile_id === "claude_code_hook"
    || connector.profile_id === "claude_code_global_context"
    || connector.profile_id === "codex_global_context"
    || connector.profile_id === "continuity_envelope_api"
    || connector.capabilities.write
}

export function isImportedSnapshot(connector: Connector): boolean {
  return IMPORTED_SOURCE_PROFILES.has(connector.profile_id) && !watchesSourceChanges(connector)
}

export interface ConnectorSourceRole {
  label: string
  warning: string | null
}

export function connectorSourceRole(connector: Connector): ConnectorSourceRole {
  const path = (connector.source_path ?? "").replaceAll("\\", "/").toLocaleLowerCase()
  if (["codex_jsonl", "claude_jsonl", "conversation_text", "transcript_json", "transcript_jsonl"].includes(connector.profile_id)) {
    return {
      label: t("历史对话文件"),
      warning: t("这是导入的对话档案，不是实时 Agent 接入；新会话通过 Hook/MCP 接入，旧档案可按需手动重扫。"),
    }
  }
  if (["cursor_sqlite", "gemini_sqlite"].includes(connector.profile_id)) {
    return {
      label: t("本地对话数据库快照"),
      warning: t("这是本地会话数据的导入快照，不是实时 Agent 接入；可按需手动重扫。"),
    }
  }
  if (["claude_memory_markdown", "codex_memory_markdown"].includes(connector.profile_id)) {
    if (path.endsWith("/memory_summary.md")) {
      return {
        label: t("派生摘要"),
        warning: t("它可能重复上游记忆索引；不需要时可暂停此来源。"),
      }
    }
    if (path.endsWith("/memory.md")) {
      return {
        label: t("Agent 记忆索引"),
        warning: t("这是 Agent 自己维护的记忆索引，只读导入并独立治理。"),
      }
    }
    return {
      label: t("Agent 原生记忆文件（只读）"),
      warning: t("只读读取 Agent 自己维护的记忆文件；它不是实时对话 Hook。"),
    }
  }
  if (connector.profile_id.endsWith("_memory_markdown") && path.endsWith("/memory.md")) {
    return {
      label: t("Agent 记忆索引"),
      warning: t("这是未验证的 Agent 记忆来源，写入前按待确认内容治理。"),
    }
  }
  if (connector.profile_id.endsWith("_memory_markdown")) {
    return {
      label: t("Agent 记忆文件"),
      warning: t("这是未验证的 Agent 记忆来源，写入前按待确认内容治理。"),
    }
  }
  if (connector.profile_id.includes("jsonl") || connector.profile_id.includes("sqlite")) {
    return { label: t("对话或活动记录"), warning: null }
  }
  if (connector.profile_id === "claude_code_hook") {
    return { label: t("实时项目证据"), warning: null }
  }
  if (connector.profile_id === "claude_code_global_context") {
    return { label: t("只读个人上下文"), warning: null }
  }
  if (connector.capabilities.write && !connector.capabilities.read) {
    return { label: t("受管上下文交付"), warning: null }
  }
  return { label: t("本地来源"), warning: null }
}

export function connectorLocation(connector: Connector): string {
  if (connector.source_path) return connector.source_path
  if (connector.target_path) return connector.target_path
  return t("通过本机 API 交换")
}

export function connectorPurpose(connector: Connector): string {
  if (connector.profile_id === "claude_code_global_context") return t("自动交付个人上下文，不采集全局对话")
  if (connector.profile_id === "claude_code_hook") return t("自动接收该项目的对话证据")
  if (connector.profile_id === "continuity_envelope_api") return t("接收经过认证的结构化证据")
  if (connector.capabilities.read && connector.capabilities.write) return t("读取来源并更新受管上下文")
  if (connector.capabilities.write) return t("向 Agent 交付受管上下文")
  return t("从本地来源导入记忆")
}
