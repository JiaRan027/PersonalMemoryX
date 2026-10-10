<script setup lang="ts">
import {
  Archive,
  Bot,
  BriefcaseBusiness,
  Check,
  CheckCircle2,
  FileQuestion,
  FileText,
  FlaskConical,
  Folder,
  FolderOpen,
  Languages,
  MapPin,
  Pencil,
  Quote,
  Settings2,
  Sparkles,
  Trash2,
  X,
} from "lucide-vue-next"
import { invoke } from "@tauri-apps/api/core"
import { computed, ref, watch } from "vue"

import type { MemoryItem, ReviewVerdict } from "@/api/client"
import { useI18n } from "@/i18n"
import { evidenceLocation, provenanceFor, sourceFileName, sourceName } from "@/memories/provenance"
import { formatLocalDateTime } from "@/utils/dateTime"
import { needsMemoryReview, triageReasonLabel } from "@/memories/presentation"

const { isEnglish, t } = useI18n()

const props = defineProps<{ memory: MemoryItem; busy?: boolean }>()
const emit = defineEmits<{
  close: []
  revise: [statement: string, reason: string | null]
  delete: []
  review: [verdict: ReviewVerdict, scopeHint: string | null]
}>()
const editing = ref(false)
const statement = ref(props.memory.statement)
const reason = ref(props.memory.reason ?? "")
const provenance = computed(() => provenanceFor(props.memory))
const provenanceName = computed(() => sourceName(provenance.value))
const provenanceFileName = computed(() => sourceFileName(provenance.value.locator))
const provenanceLocation = computed(() => evidenceLocation(provenance.value))
const sourceFilePath = computed(() => {
  const locator = provenance.value.locator
  if (!locator || !/^(?:[A-Za-z]:[\\/]|\\\\)/.test(locator)) return null
  return locator
})
const sourceActionMessage = ref("")
const reviewAssistance = computed(() => props.memory.review_assistance ?? null)
const reviewDecision = computed(() => props.memory.review_decision ?? null)
const currentReviewDecision = computed(() => reviewDecision.value?.memory_revision === props.memory.revision ? reviewDecision.value : null)
const triage = computed(() => props.memory.review_triage ?? null)
const isUnverifiedHighImpact = computed(() => triage.value?.reason_codes.includes("UNVERIFIED_DETERMINISTIC_HIGH_IMPACT") ?? false)
const showAdjust = ref(false)
const requiresDecision = computed(() => needsMemoryReview(props.memory))
const showReviewOptions = computed(() => requiresDecision.value || showAdjust.value)

const reviewOptions = [
  { value: "keep", label: "保留为长期记忆", description: "这是可信、稳定且跨任务仍有价值的内容。", icon: CheckCircle2 },
  { value: "project_experience", label: "项目经验，可跨项目联想", description: "保留具体情境，并提炼适用条件，让相似问题能够联想到它。", icon: BriefcaseBusiness },
  { value: "rewrite", label: "提炼后再保留", description: "原文太像日志或过于具体，需要整理成可复用经验。", icon: Sparkles },
  { value: "source_only", label: "只保留来源材料", description: "原始记录仍有查阅价值，但不应被 Agent 当作长期记忆。", icon: Archive },
  { value: "test_artifact", label: "这是测试记录", description: "来自验收提示词、模拟数据或功能测试，不代表真实偏好。", icon: FlaskConical },
  { value: "needs_context", label: "证据不足，稍后判断", description: "当前缺少原始对话、形成原因或明确主体，不能可靠定性。", icon: FileQuestion },
] satisfies Array<{ value: ReviewVerdict; label: string; description: string; icon: typeof CheckCircle2 }>

const typeLabels: Record<string, string> = { fact: "事实", goal: "目标", skill: "能力", preference: "偏好", event: "经历", insight: "洞察", decision: "决策" }
const statusLabels: Record<string, string> = { asserted: "已确认", inferred: "系统推断" }
const recordStatusLabels: Record<string, string> = { active: "当前有效", superseded: "已被新版替代", deleted: "已删除", quarantined: "未进入记忆库" }
const sensitivityLabels: Record<string, string> = { normal: "普通", personal: "个人", sensitive: "敏感", restricted: "受限" }
function typeLabel(type: string): string { return typeLabels[type] ? t(typeLabels[type]) : type }
function statusLabel(state: string): string { return t(statusLabels[state] ?? "待确认") }
function usageStatusLabel(state: string): string {
  if (triage.value && ["source_only", "test_artifact"].includes(triage.value.effective_verdict)) {
    return t("不作为长期记忆")
  }
  return statusLabel(state)
}
function namespaceLabel(value: string): string { return value === "general" || value === "default" ? t("通用") : value }

function recommendationLabel(value: "keep" | "remove" | "rewrite"): string {
  return t({ keep: "建议保留", remove: "建议移出长期记忆", rewrite: "建议提炼后保留" }[value])
}

function captureLabel(value: string | null): string | null {
  if (!value) return null
  const labels: Record<string, string> = {
    realtime_hook: "实时 Hook",
    structured_api: "结构化 API",
    offline_import: "离线导入",
    manual: "手动记录",
  }
  return labels[value] ? t(labels[value]) : value
}

function actorLabel(): string | null {
  const value = provenance.value
  if (value.evidence_actor === "user" || value.actor_kind === "user") return t("用户明确表达")
  if (value.evidence_actor === "agent_memory") return t("Agent 原生记忆")
  if (value.actor_kind === "agent") return value.actor_id ? `Agent: ${value.actor_id}` : t("Agent 推断")
  return value.actor_id
}

function qualityIssueMessage(code: string): string {
  if (code === "CROSS_NAMESPACE_EXACT_DUPLICATE") return t("这条记忆与其他适用范围中的内容完全重复。请核对适用范围，避免在多个范围保存相同信息。")
  if (code === "AGENT_WORKFLOW_NOTE") return t("这条内容来自 Agent 的运维索引或工作备忘，尚未被提炼为可长期复用的经验。")
  if (code === "MISSING_DECISION_RATIONALE") return t("这条决策缺少形成原因；如果未来无法解释为什么这样决定，它不适合直接交给新的 Agent。")
  return t("质量检查提示：{code}", { code })
}

function verdictLabel(value: ReviewVerdict): string {
  const label = reviewOptions.find((option) => option.value === value)?.label
  return label ? t(label) : value
}

function triageHeading(): string {
  if (!triage.value) return t("等待系统治理")
  if (triage.value.overridden_by_user) return t("已按你的判断处理")
  return triage.value.requires_user_review ? t("需要你决定") : t("系统已自动处理")
}

watch(() => props.memory, (memory) => {
  statement.value = memory.statement
  reason.value = memory.reason ?? ""
  editing.value = false
  showAdjust.value = false
  sourceActionMessage.value = ""
})

async function openSourceFile(): Promise<void> {
  if (!sourceFilePath.value) return
  try {
    await invoke("open_source_file", { path: sourceFilePath.value })
    sourceActionMessage.value = t("已在资源管理器中定位来源文件")
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error)
    sourceActionMessage.value = detail.includes("invoke") || detail.includes("undefined")
      ? t("当前是浏览器预览版；桌面版可直接定位来源文件，路径已显示在上方。")
      : detail
  }
}
</script>

<template>
  <aside class="detail" :aria-label="t('记忆详情')">
    <header>
      <div><span class="type-label">{{ typeLabel(memory.type) }}</span><h2>{{ t("记忆详情") }}</h2></div>
      <div class="header-actions">
        <button class="icon-button danger" type="button" :aria-label="t('删除这条记忆')" :title="t('删除这条记忆')" :disabled="busy" @click="$emit('delete')"><Trash2 :size="17" /></button>
        <button v-if="!editing" class="command-button" type="button" :disabled="busy" :aria-label="t('纠正这条记忆')" :title="t('纠正这条记忆')" @click="editing = true"><Pencil :size="16" />{{ t("纠正") }}</button>
        <button v-else class="icon-button" type="button" :aria-label="t('取消纠正')" :title="t('取消纠正')" :disabled="busy" @click="editing = false"><X :size="17" /></button>
        <button v-if="editing" class="command-button primary" type="button" :disabled="busy" :aria-label="t('保存纠正')" :title="t('保存纠正')" @click="$emit('revise', statement, reason || null)"><Check :size="16" />{{ busy ? t("保存中...") : t("保存纠正") }}</button>
        <button class="icon-button" type="button" :aria-label="t('关闭详情')" :title="t('关闭详情')" :disabled="busy" @click="$emit('close')"><X :size="17" /></button>
      </div>
    </header>
    <div class="detail-body">
      <section v-if="reviewAssistance" class="review-assistance" :aria-label="t('中文辅助理解')">
        <div class="review-heading"><h3><Languages :size="16" />{{ t("中文辅助理解") }}</h3><span>{{ t("不写入记忆") }}</span></div>
        <p class="review-summary">{{ reviewAssistance.summary }}</p>
        <p v-if="reviewAssistance.explanation" class="review-explanation">{{ reviewAssistance.explanation }}</p>
        <div class="review-recommendation" :data-recommendation="reviewAssistance.recommendation">
          <strong>{{ recommendationLabel(reviewAssistance.recommendation) }}</strong>
          <span>{{ reviewAssistance.recommendation_reason }}</span>
        </div>
        <p class="review-disclaimer">{{ t("这是外部 Agent 提供的审核辅助，不改变英文原文、证据、版本或确认状态。") }}</p>
      </section>
      <section class="system-decision" :class="{ attention: requiresDecision }" aria-labelledby="system-decision-heading">
        <div class="system-decision-heading">
          <div>
            <span>{{ triageHeading() }}</span>
            <h3 id="system-decision-heading">{{ triage ? verdictLabel(triage.effective_verdict) : t("尚未获得治理结果") }}</h3>
          </div>
          <strong v-if="triage" :class="{ excluded: !triage.delivery_eligible }">{{ triage.delivery_eligible ? t("会提供给 Agent") : t("不会进入 Agent 上下文") }}</strong>
        </div>
        <p v-if="triage?.reason_codes.length" class="system-reasons">{{ triage.reason_codes.map((code) => t(triageReasonLabel(code), code.startsWith("QUALITY:") ? { code: code.slice(8) } : {})).join(isEnglish ? "; " : "；") }}{{ isEnglish ? "." : "。" }}</p>
        <p v-if="isUnverifiedHighImpact" class="system-impact-note">{{ t("系统没有把这句话认定为你的真实身份或偏好。它只作为可追溯的来源片段保留，不会用于 Agent 回答。") }}</p>
        <p v-if="!triage" class="system-reasons">{{ t("当前服务没有返回自动治理结果，请刷新或检查服务版本。") }}</p>
        <button v-if="!requiresDecision && !showAdjust" class="adjust-decision" type="button" :disabled="busy" @click="showAdjust = true"><Settings2 :size="15" />{{ t("调整系统判断") }}</button>
      </section>
      <section v-if="showReviewOptions" class="review-decision" aria-labelledby="review-decision-heading">
        <div class="review-decision-heading">
          <div>
            <h3 id="review-decision-heading">{{ requiresDecision ? t("请选择它应当如何使用") : t("调整系统判断") }}</h3>
            <p>{{ t("这不是在给数据贴标签，而是在纠正系统无法安全决定的用途。") }}</p>
          </div>
          <span v-if="currentReviewDecision">{{ t("已记录") }}</span>
        </div>
        <p class="review-principle">{{ t("你的选择会覆盖交付规则，但不会静默删除原始记录；来源证据仍可追溯。") }}</p>
        <div class="review-options" role="group" :aria-label="t('选择处理方式')">
          <button
            v-for="option in reviewOptions"
            :key="option.value"
            class="review-option"
            :class="{ selected: currentReviewDecision?.verdict === option.value }"
            type="button"
            :disabled="busy"
            :aria-pressed="currentReviewDecision?.verdict === option.value"
            @click="$emit('review', option.value, option.value === 'project_experience' ? (provenance.project || memory.namespace) : null)"
          >
            <component :is="option.icon" :size="16" />
            <span><strong>{{ t(option.label) }}</strong><small>{{ t(option.description) }}</small></span>
          </button>
        </div>
        <button v-if="!requiresDecision" class="adjust-decision" type="button" :disabled="busy" @click="showAdjust = false">{{ t("收起调整选项") }}</button>
        <p v-if="reviewDecision && !currentReviewDecision" class="review-stale">{{ t("这条记忆已在审核后更新，请重新判断当前版本。") }}</p>
        <p v-if="currentReviewDecision?.scope_hint" class="scope-hint">{{ t("来源项目：{project}", { project: currentReviewDecision.scope_hint }) }}</p>
      </section>
      <label>{{ reviewAssistance ? t("英文原文") : t("内容") }}<textarea v-if="editing" v-model="statement" rows="5" :disabled="busy" /><p v-else class="statement">{{ memory.statement }}</p></label>
      <label>{{ t("形成原因") }}<textarea v-if="editing" v-model="reason" rows="4" :disabled="busy" /><p v-else>{{ memory.reason || t("未记录形成原因") }}</p></label>
      <p v-for="(issue, index) in memory.quality_issues ?? []" :key="`${issue.code}-${index}`" class="quality-warning">{{ qualityIssueMessage(issue.code) }}<span v-if="issue.related_namespace"> {{ t("相关适用范围：{scope}。", { scope: namespaceLabel(issue.related_namespace) }) }}</span></p>
      <section class="provenance-section" aria-labelledby="provenance-heading">
        <h3 id="provenance-heading"><MapPin :size="15" />{{ t("来源与证据") }}</h3>
        <div class="source-card">
          <div class="source-summary">
            <Bot :size="17" />
            <div><strong>{{ provenanceName }}</strong><span v-if="captureLabel(provenance.capture_mode)">{{ captureLabel(provenance.capture_mode) }}</span></div>
          </div>
          <div v-if="provenance.locator" class="source-row">
            <FileText :size="15" />
            <div>
              <span>{{ provenanceFileName || t("来源记录") }}</span>
              <code>{{ provenance.locator }}</code>
              <button v-if="sourceFilePath" class="source-open" type="button" :disabled="busy" @click="openSourceFile"><FolderOpen :size="14" />{{ t("打开来源文件") }}</button>
              <small v-else class="source-note">{{ t("这是对话定位符，不是可直接打开的本地文件。") }}</small>
            </div>
          </div>
          <div v-if="provenance.project" class="source-row">
            <Folder :size="15" />
            <div><span>{{ t("所在项目") }}</span><code>{{ provenance.project }}</code></div>
          </div>
          <div class="source-facts">
            <span>{{ provenanceLocation }}</span>
            <span v-if="provenance.source_section">{{ provenance.source_section }}</span>
            <span v-if="actorLabel()">{{ actorLabel() }}</span>
          </div>
          <blockquote v-if="provenance.evidence_excerpt">
            <Quote :size="15" />
            <p>{{ provenance.evidence_excerpt }}</p>
          </blockquote>
          <p v-else class="evidence-empty">{{ t("这条记忆没有保存可展示的原文片段。") }}</p>
          <p v-if="sourceActionMessage" class="source-action-message">{{ sourceActionMessage }}</p>
        </div>
      </section>
      <dl>
        <div><dt>{{ t("类型") }}</dt><dd>{{ typeLabel(memory.type) }}</dd></div>
        <div><dt>{{ t("系统使用状态") }}</dt><dd>{{ usageStatusLabel(memory.epistemic_state) }}</dd></div>
        <div><dt>{{ t("创建时间") }}</dt><dd><time :datetime="memory.created_at">{{ formatLocalDateTime(memory.created_at) }}</time></dd></div>
        <div><dt>{{ t("更新时间") }}</dt><dd><time :datetime="memory.updated_at">{{ formatLocalDateTime(memory.updated_at) }}</time></dd></div>
        <div><dt>{{ t("版本") }}</dt><dd>{{ t("第 {revision} 版", { revision: memory.revision }) }}</dd></div>
        <div><dt>{{ t("记录状态") }}</dt><dd>{{ t(recordStatusLabels[memory.status] ?? "需要检查") }}</dd></div>
        <div><dt>{{ t("适用范围") }}</dt><dd>{{ namespaceLabel(memory.namespace) }}</dd></div>
        <div><dt>{{ t("敏感级别") }}</dt><dd>{{ t(sensitivityLabels[memory.sensitivity] ?? "未分类") }}</dd></div>
      </dl>
    </div>
  </aside>
</template>

<style scoped>
.detail { position: sticky; top: 20px; display: flex; min-height: 520px; flex-direction: column; border-left: 1px solid var(--border); background: var(--surface); }
header { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; padding: 18px 20px; border-bottom: 1px solid var(--border); }
.header-actions { display: flex; flex: none; flex-wrap: wrap; align-items: center; justify-content: flex-end; gap: 8px; }
.header-actions .danger { color: var(--danger); }
h2 { margin: 4px 0 0; font-size: 17px; }
.type-label { color: var(--brand); font-size: 11px; font-weight: 700; text-transform: uppercase; }
.detail-body { display: grid; gap: 22px; padding: 20px; }
.quality-warning { margin: 0; padding: 10px 12px; border: 1px solid #e5c878; background: var(--warning-soft, #fff8e1); color: #745b13; font-size: 12px; line-height: 1.5; }
.review-assistance { display: grid; gap: 10px; padding: 12px; border: 1px solid #a8cfc0; border-radius: var(--radius); background: #f1f8f5; }
.review-heading { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.review-heading h3 { display: flex; align-items: center; gap: 6px; margin: 0; color: var(--text); font-size: 13px; }
.review-heading > span { padding: 2px 5px; border: 1px solid #b9d8c3; border-radius: 3px; color: #2e6d59; font-size: 9px; }
.review-summary { margin: 0; color: var(--text); font-size: 15px; line-height: 1.65; }
.review-explanation,
.review-disclaimer { margin: 0; color: var(--text-muted); font-size: 11px; line-height: 1.55; }
.review-recommendation { display: grid; gap: 3px; padding: 9px; border-left: 3px solid #598b78; background: var(--surface); }
.review-recommendation[data-recommendation="remove"] { border-left-color: #c06a5b; }
.review-recommendation[data-recommendation="rewrite"] { border-left-color: #b48a32; }
.review-recommendation strong { color: var(--text); font-size: 12px; }
.review-recommendation span { color: var(--text-muted); font-size: 11px; line-height: 1.5; }
.system-decision { display: grid; gap: 10px; padding: 12px; border: 1px solid #b9d8c3; border-radius: var(--radius); background: #f1f8f5; }
.system-decision.attention { border-color: #e5c878; background: var(--warning-soft, #fff8e1); }
.system-decision-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.system-decision-heading > div { display: grid; gap: 3px; }
.system-decision-heading span { color: var(--text-muted); font-size: 10px; }
.system-decision-heading h3 { margin: 0; color: var(--text); font-size: 14px; }
.system-decision-heading strong { flex: none; padding: 3px 6px; border: 1px solid #a8cfc0; border-radius: 3px; color: #2e6d59; font-size: 10px; }
.system-decision-heading strong.excluded { border-color: var(--border-strong); color: var(--text-muted); }
.system-reasons { margin: 0; color: var(--text-muted); font-size: 11px; line-height: 1.55; }
.system-impact-note { margin: 0; padding: 8px 10px; border-left: 3px solid var(--border-strong); background: var(--surface); color: var(--text); font-size: 11px; line-height: 1.55; }
.adjust-decision { display: inline-flex; width: fit-content; align-items: center; gap: 5px; padding: 0; border: 0; background: transparent; color: var(--brand); font-size: 11px; }
.review-decision { display: grid; gap: 10px; }
.review-decision-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 10px; }
.review-decision-heading h3 { margin: 0; color: var(--text); font-size: 14px; }
.review-decision-heading p,
.review-principle,
.scope-hint { margin: 3px 0 0; color: var(--text-muted); font-size: 11px; line-height: 1.5; }
.review-decision-heading > span { flex: none; padding: 2px 6px; border: 1px solid #a8cfc0; border-radius: 3px; color: #2e6d59; font-size: 10px; }
.review-principle { margin: 0; padding: 8px 10px; border-left: 3px solid var(--warning); background: var(--warning-soft, #fff8e1); color: #745b13; }
.review-stale { margin: 0; color: var(--warning); font-size: 11px; }
.review-options { display: grid; gap: 6px; }
.review-option { display: grid; grid-template-columns: 20px minmax(0, 1fr); gap: 8px; align-items: start; width: 100%; padding: 9px 10px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface); color: var(--text-muted); text-align: left; }
.review-option:hover { border-color: var(--border-strong); background: var(--surface-subtle); }
.review-option.selected { border-color: var(--brand); background: var(--brand-soft); color: var(--brand); }
.review-option svg { margin-top: 1px; }
.review-option span { display: grid; gap: 2px; }
.review-option strong { color: var(--text); font-size: 12px; }
.review-option small { color: var(--text-muted); font-size: 10px; line-height: 1.45; }
.scope-hint { overflow-wrap: anywhere; }
label { display: grid; gap: 7px; color: var(--text-muted); font-size: 12px; font-weight: 600; }
label p { margin: 0; color: var(--text); font-size: 14px; font-weight: 400; line-height: 1.65; }
.statement { font-size: 16px; }
textarea { width: 100%; resize: vertical; padding: 9px; border: 1px solid var(--border-strong); border-radius: var(--radius); color: var(--text); }
.provenance-section { display: grid; gap: 8px; }
.provenance-section h3 { display: flex; align-items: center; gap: 6px; margin: 0; color: var(--text); font-size: 13px; }
.source-card { display: grid; gap: 11px; padding: 12px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface-subtle); }
.source-summary,
.source-row { display: flex; min-width: 0; align-items: flex-start; gap: 9px; }
.source-summary > svg,
.source-row > svg { flex: none; margin-top: 2px; color: var(--text-muted); }
.source-summary div,
.source-row div { display: grid; min-width: 0; gap: 3px; }
.source-summary strong { color: var(--text); font-size: 13px; }
.source-summary span,
.source-row span { color: var(--text-muted); font-size: 11px; }
.source-row code { overflow-wrap: anywhere; color: var(--text); font-family: Consolas, monospace; font-size: 11px; line-height: 1.45; white-space: normal; }
.source-open { display: inline-flex; width: fit-content; align-items: center; gap: 5px; margin-top: 3px; padding: 3px 7px; border: 1px solid var(--border-strong); border-radius: 3px; background: var(--surface); color: var(--brand); font-size: 11px; }
.source-open:hover { background: var(--brand-soft); }
.source-note, .source-action-message { color: var(--text-muted); font-size: 10px; line-height: 1.45; }
.source-action-message { margin: 0; color: var(--brand); }
.source-facts { display: flex; flex-wrap: wrap; gap: 5px; }
.source-facts span { padding: 3px 6px; border: 1px solid var(--border); border-radius: 3px; background: var(--surface); color: var(--text-muted); font-size: 10px; }
blockquote { display: flex; min-width: 0; gap: 8px; margin: 0; padding: 10px; border-left: 2px solid var(--brand); background: var(--surface); }
blockquote svg { flex: none; color: var(--text-muted); }
blockquote p { min-width: 0; margin: 0; overflow-wrap: anywhere; color: var(--text); font-size: 12px; line-height: 1.55; }
.evidence-empty { margin: 0; color: var(--text-muted); font-size: 11px; }
dl { display: grid; gap: 0; margin: 0; }
dl div { display: flex; justify-content: space-between; gap: 16px; padding: 10px 0; border-bottom: 1px solid var(--border); }
dt { color: var(--text-muted); }
dd { margin: 0; overflow-wrap: anywhere; text-align: right; }
@media (max-width: 760px) {
  .detail { position: static; min-height: 0; border-top: 1px solid var(--border); border-left: 0; }
  header { align-items: center; }
  .header-actions { flex-wrap: wrap; justify-content: flex-end; }
}
</style>
