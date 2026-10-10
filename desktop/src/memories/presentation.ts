import type { MemoryItem } from "@/api/client"

const TRANSIENT_PROCESS_PATTERNS = [
  /^(?:now|currently)\s+(?:exploring|delving|focus(?:ed|ing)|working\s+on|investigating|testing|reviewing|analyzing|checking|looking\s+into|implementing|updating|running)\b/i,
  /^(?:接下来|下一步)\s*(?:开始|继续|检查|分析|探索|测试|实现|更新|处理|运行|查看)/i,
]

export function isLikelyProcessNarration(memory: MemoryItem): boolean {
  return TRANSIENT_PROCESS_PATTERNS.some((pattern) => pattern.test(memory.statement.trim()))
}

export function needsMemoryReview(memory: MemoryItem): boolean {
  if (memory.review_triage) return memory.review_triage.requires_user_review
  return memory.epistemic_state !== "asserted"
    || isLikelyProcessNarration(memory)
    || Boolean(memory.quality_issues?.length)
}

export function isSystemHandled(memory: MemoryItem): boolean {
  return Boolean(memory.review_triage && !memory.review_triage.requires_user_review)
}

export function isDeliveryExcluded(memory: MemoryItem): boolean {
  return memory.review_triage?.delivery_eligible === false
}

export function triageReasonLabel(code: string): string {
  const labels: Record<string, string> = {
    USER_OVERRIDE: "按你的判断处理",
    NON_CANONICAL_DUPLICATE: "与其他范围的记忆重复，已保留更可信的一条",
    AGENT_WORKFLOW_NOTE: "属于 Agent 工作索引或运维备忘",
    TEST_SOURCE: "来自测试或验收数据",
    TRANSIENT_PROCESS: "属于临时工作过程",
    OPERATIONAL_INDEX: "属于来源目录或运维索引，具体经验已由关联记忆承载",
    SENSITIVE_INFERENCE: "涉及敏感信息，且不是你明确确认的内容",
    TRUSTED_USER_EVIDENCE: "有用户明确表达的证据",
    ASSERTED_CORE: "已经进入受治理的正式记忆",
    UNVERIFIED_AGENT_HIGH_IMPACT: "Agent 推断了重要的个人事实、目标或偏好",
    UNVERIFIED_AGENT_SOURCE: "来自未经用户确认的 Agent 原生记忆",
    UNVERIFIED_DETERMINISTIC_HIGH_IMPACT: "来源主体无法确认的重要身份、事实或偏好",
    PROVISIONAL_WITH_EVIDENCE: "有原始证据，可作为项目经验使用",
    GOVERNED_PROVISIONAL: "来自既有受治理记录，兼容保留交付",
    MISSING_DECISION_RATIONALE: "决策缺少形成原因",
  }
  if (code.startsWith("QUALITY:")) return "仍有质量问题：{code}"
  return labels[code] ?? code
}
