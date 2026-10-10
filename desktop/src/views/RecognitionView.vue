<script setup lang="ts">
import { RefreshCw, ScanSearch } from "lucide-vue-next"
import { computed, onMounted, ref } from "vue"

import {
  requireClient,
  type ExtractPreviewItem,
  type ImportResponse,
  type RecognizedSource,
} from "@/api/client"
import { importNoticeText, summarizeImports } from "@/recognition/importSummary"
import { useI18n } from "@/i18n"

const { t } = useI18n()

const sources = ref<RecognizedSource[]>([])
const scanning = ref(false)
const error = ref("")
const previewingPath = ref<string | null>(null)
const previewItems = ref<ExtractPreviewItem[]>([])
const previewWarnings = ref<string[]>([])
const previewSource = ref<RecognizedSource | null>(null)
const importingPath = ref<string | null>(null)
const importingAll = ref(false)
const importNotice = ref("")
const importError = ref("")
const showMemoriesLink = ref(false)

function profileFor(source: RecognizedSource): string {
  if (source.format === "markdown") {
    if (source.agent === "codex") return "codex_memory_markdown"
    if (source.agent === "claude_code") return "claude_memory_markdown"
    return "generic_markdown_readonly"
  }
  if (source.format === "jsonl") {
    if (source.agent === "codex") return "codex_jsonl"
    if (source.agent === "claude_code" || source.agent === "claude_desktop") return "claude_jsonl"
    return "conversation_text"
  }
  if (source.format === "sqlite") {
    if (source.agent === "gemini_cli") return "gemini_sqlite"
    if (source.agent === "cursor") return "cursor_sqlite"
  }
  return ""
}

function canPreview(source: RecognizedSource): boolean {
  return Boolean(profileFor(source))
}

function agentName(agent: string): string {
  const names: Record<string, string> = { codex: "Codex", claude_code: "Claude Code", claude_desktop: "Claude", gemini_cli: "Gemini CLI", cursor: "Cursor" }
  return names[agent] ?? "其他 AI 工具"
}

function kindName(kind: string): string {
  const names: Record<string, string> = { memory: "记忆文件", memory_note: "原生记忆文件", conversation: "对话记录", conversation_log: "对话历史", session_store: "会话记录", state_store: "应用内部状态", history: "历史记录", project: "项目资料", settings: "配置资料" }
  return names[kind] ?? "已识别资料"
}

function formatName(format: string): string {
  const names: Record<string, string> = { markdown: "Markdown 文档", jsonl: "JSONL 记录", sqlite: "本地数据库", leveldb: "LevelDB 内部数据", protobuf: "Protobuf 内部数据" }
  return names[format] ?? "暂不支持的格式"
}

function memoryTypeName(type: string): string {
  const names: Record<string, string> = { fact: "事实", goal: "目标", skill: "能力", preference: "偏好", event: "经历", insight: "洞察", decision: "决策" }
  return names[type] ?? "其他"
}

function sourceSignalName(signal: string): string {
  const names: Record<string, string> = {
    deterministic: "本地规则识别",
    llm: "可选模型提取",
    user_message: "用户明确表达",
    assistant_message: "Agent 回答或总结",
    agent_memory: "Agent 原生记忆",
  }
  return names[signal] ?? "来源未分类"
}

function admissionActionName(action?: string | null): string | null {
  const names: Record<string, string> = {
    ACCEPT: "可接纳", ACCEPTED: "可接纳", ADMITTED: "可接纳",
    PROVISIONAL: "需确认", NEEDS_CONFIRMATION: "需确认", REVIEW: "需确认",
    QUARANTINE: "存在冲突", CONFLICT: "存在冲突", QUARANTINED: "存在冲突",
    RESTRICT: "受限", RESTRICTED: "受限",
    DISCARD: "不会导入", REJECTED: "不会导入", SKIPPED: "不会导入",
  }
  return action ? (names[action.toUpperCase()] ?? "需确认") : null
}

function admissionReasonName(code?: string | null): string | null {
  const names: Record<string, string> = {
    TRANSIENT_INFORMATION_REQUEST: "这是一次临时信息请求，不适合作为长期记忆。",
    AGENT_INFERENCE: "内容来自 Agent 推断，需要你确认后才会作为长期记忆。",
    UNCONFIRMED_OBSERVATION: "这是一条尚未确认的观察，需要进一步核对。",
    MALFORMED_SELF_STATEMENT: "内容只有自述前缀，没有可保存的长期信息，因此不会导入。",
    QUESTION_SHAPED_CANDIDATE: "这是一句提问，不是关于你的长期记忆，因此不会导入。",
    CROSS_NAMESPACE_EXACT_DUPLICATE: "该内容与其他适用范围中的记忆完全重复；导入后会保留来源与适用范围并标为需检查，交付给 Agent 时只发送一份。",
    TECHNICAL_RULE_NOT_USER_PREFERENCE: "这是一条技术实现规则，不等同于你的个人偏好；已按技术规则重新分类。",
  }
  return code ? (names[code] ?? `准入原因：${code}`) : null
}

function classificationReasonName(extra?: Record<string, unknown> | null): string | null {
  const code = extra?.classification_reason_code
  if (code === "TECHNICAL_RULE_NOT_USER_PREFERENCE") {
    return "这是一条技术实现规则，不等同于你的个人偏好；已按技术规则重新分类为洞察。"
  }
  return null
}

const supportedSources = computed(() => sources.value.filter(canPreview))
const unsupportedSources = computed(() => sources.value.filter((source) => !canPreview(source)))

async function importSource(source: RecognizedSource) {
  const profileId = profileFor(source)
  if (!profileId) return
  importingPath.value = source.path
  importError.value = ""
  importNotice.value = ""
  showMemoriesLink.value = false
  try {
    const result = await requireClient().importSource(profileId, source.path, source.agent)
    showMemoriesLink.value = true
    if (result.failed > 0) importError.value = `有 ${result.failed} 个文件导入失败，其余已处理。`
    if (result.ingested > 0) importNotice.value = `已新增 ${result.ingested} 条记忆（共处理 ${result.total} 个文件）。`
    else if (result.processed > 0) importNotice.value = `${result.processed} 个文件已处理，但没有提取出候选记忆。`
    else if (result.skipped > 0) importNotice.value = `${result.skipped} 个文件超过大小上限被跳过。`
    else importNotice.value = "来源已经是最新状态，没有新增内容。"
  } catch {
    importError.value = "导入失败，请确认文件可读且格式正确。"
  } finally {
    importingPath.value = null
  }
}

function formatBytes(value: number): string {
  if (value >= 1024 * 1024) return `${(value / (1024 * 1024)).toFixed(1)} MB`
  if (value >= 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${value} B`
}

async function importAllSources() {
  const targets = supportedSources.value
  if (!targets.length) return
  importingAll.value = true
  importError.value = ""
  importNotice.value = ""
  showMemoriesLink.value = false
  const results: ImportResponse[] = []
  let requestErrors = 0
  for (const source of targets) {
    try { results.push(await requireClient().importSource(profileFor(source), source.path, source.agent)) }
    catch { requestErrors += 1 }
  }
  const summary = summarizeImports(results)
  importNotice.value = importNoticeText(summary, requestErrors)
  showMemoriesLink.value = true
  if (summary.failed + requestErrors > 0) importError.value = "部分来源导入失败，其余已处理。"
  importingAll.value = false
}

async function scan() {
  scanning.value = true
  error.value = ""
  previewItems.value = []
  previewWarnings.value = []
  previewSource.value = null
  try { sources.value = (await requireClient().recognizeSources()).items }
  catch { sources.value = []; error.value = t("扫描本机记忆失败，请确认服务正在运行。") }
  finally { scanning.value = false }
}

async function preview(source: RecognizedSource) {
  previewingPath.value = source.path
  error.value = ""
  previewItems.value = []
  previewWarnings.value = []
  previewSource.value = source
  try {
    const response = await requireClient().extractPreview(profileFor(source), source.path)
    previewItems.value = response.items
    previewWarnings.value = response.warnings
  } catch { error.value = t("提取预览失败，请稍后重试。") }
  finally { previewingPath.value = null }
}

onMounted(scan)
</script>

<template>
  <section class="page">
    <header class="page-header">
      <div>
        <h1 class="page-title">{{ t("记忆识别") }}</h1>
        <p class="page-subtitle">{{ t("只读扫描本机 AI 工具的记忆与会话文件，不会修改任何原始文件。") }}</p>
      </div>
      <div class="header-actions">
        <a class="command-button" href="#/connections">{{ t("返回来源") }}</a>
        <button class="command-button" type="button" :disabled="scanning || importingAll || !supportedSources.length" @click="importAllSources">
          {{ importingAll ? t("导入中...") : t("导入可用来源（{count}）", { count: supportedSources.length }) }}
        </button>
        <button class="command-button primary" type="button" :disabled="scanning" @click="scan"><ScanSearch :size="17" />{{ scanning ? t("扫描中...") : t("扫描本机记忆") }}</button>
      </div>
    </header>

    <p v-if="error" class="inline-error" role="alert">{{ error }}</p>
    <p v-if="importError" class="inline-error" role="alert">{{ importError }}</p>
    <p v-if="importNotice" class="inline-notice" role="status">
      {{ t(importNotice) }} <RouterLink v-if="showMemoriesLink" :to="{ name: 'memories' }">{{ t("前往记忆页检查") }}</RouterLink>
    </p>

    <section class="panel source-list" aria-labelledby="supported-sources-title">
      <div class="section-heading"><h2 id="supported-sources-title">{{ t("可预览并导入的来源") }}</h2><span>{{ t("{count} 个", { count: supportedSources.length }) }}</span></div>
      <div class="list-heading"><span>{{ t("来源") }}</span><span>{{ t("资料类型") }}</span><span>{{ t("文件格式") }}</span><span>{{ t("规模") }}</span><span>{{ t("位置与操作") }}</span></div>
      <div v-if="scanning" class="empty-state"><RefreshCw :size="18" class="spin" />{{ t("正在扫描本机 Agent 记忆...") }}</div>
      <div v-else-if="!supportedSources.length" class="empty-state">{{ t("未发现当前可预览或导入的来源。") }}</div>
      <div v-for="source in supportedSources" :key="`${source.agent}-${source.kind}-${source.path}`" class="source-row">
        <span class="agent" :title="source.agent">{{ agentName(source.agent) }}</span>
        <span class="kind" :title="source.kind">{{ kindName(source.kind) }}</span>
        <span class="format-tag" :title="source.format">{{ formatName(source.format) }}</span>
        <span class="size">{{ t("{count} 个文件 · {size}", { count: source.file_count, size: formatBytes(source.size_bytes) }) }}</span>
        <span class="path"><span class="path-text" :title="source.path">{{ source.path }}</span><button class="text-button" type="button" :disabled="previewingPath === source.path" @click="preview(source)">{{ previewingPath === source.path ? t("提取中...") : t("提取预览") }}</button><button class="text-button primary-action" type="button" :disabled="importingPath === source.path || importingAll" @click="importSource(source)">{{ importingPath === source.path ? t("导入中...") : t("导入") }}</button></span>
      </div>
    </section>

    <details v-if="unsupportedSources.length" class="unsupported-sources">
      <summary>{{ t("暂不支持的来源（{count}）", { count: unsupportedSources.length }) }}</summary>
      <p>{{ t("这些来源已被发现，但当前不能预览或导入。") }}</p>
      <div v-for="source in unsupportedSources" :key="`${source.agent}-${source.kind}-${source.path}`" class="unsupported-row">
        <strong :title="source.agent">{{ agentName(source.agent) }}</strong><span :title="source.kind">{{ kindName(source.kind) }}</span><span :title="source.format">{{ formatName(source.format) }}</span><span class="path-text" :title="source.path">{{ source.path }}</span>
      </div>
    </details>

    <section v-if="previewSource" class="panel preview-panel" aria-labelledby="preview-title">
      <div class="preview-heading"><div><h2 id="preview-title">{{ t("候选记忆") }}</h2><p>{{ t("从该来源识别出的长期信息，目前尚未写入记忆库。") }}</p></div><div class="preview-source"><strong>{{ t(agentName(previewSource.agent)) }} · {{ t(kindName(previewSource.kind)) }}</strong><span :title="previewSource.path">{{ previewSource.path }}</span></div></div>
      <p class="candidate-explanation">{{ t("导入后，每条候选还会经过重复、冲突和可信度检查；结果会出现在“活动”中，被接纳的内容可在“记忆”中纠正或删除。") }}</p>
      <div v-for="(item, index) in previewItems" :key="index" class="candidate-row">
        <span class="type-tag" :title="item.type">{{ t(memoryTypeName(item.type)) }}</span><span class="confidence">{{ t("识别把握 {percent}%", { percent: Math.round(item.confidence * 100) }) }}</span>
        <div class="candidate-text"><p>{{ item.statement }}</p><p v-if="item.reason" class="reason">提取依据：{{ item.reason }}</p><p v-if="classificationReasonName(item.extra)" class="classification-note"><strong>分类说明：</strong>{{ classificationReasonName(item.extra) }}</p><p v-if="admissionActionName(item.admission_action)" class="admission-result"><strong>准入结果：{{ admissionActionName(item.admission_action) }}</strong><span v-if="admissionReasonName(item.admission_reason_code)"> {{ admissionReasonName(item.admission_reason_code) }}</span></p><dl class="candidate-meta"><div><dt>原文位置</dt><dd>{{ item.evidence.start }}–{{ item.evidence.end }}</dd></div><div><dt>识别方式</dt><dd :title="item.source_signal">{{ sourceSignalName(item.source_signal) }}</dd></div><div><dt>长期价值</dt><dd>{{ Math.round(item.importance * 100) }}%</dd></div></dl></div>
      </div>
      <p v-if="!previewItems.length" class="empty-state">{{ t("该来源未提取出候选记忆。") }}</p>
      <p v-for="warning in previewWarnings" :key="warning" class="preview-warning">{{ warning }}</p>
    </section>
  </section>
</template>

<style scoped>
.page-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; margin-bottom: 20px; }
.header-actions { display: flex; align-items: center; gap: 10px; }
.source-list { overflow: hidden; }
.section-heading, .preview-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; padding: 13px 16px; border-bottom: 1px solid var(--border); }
.section-heading h2, .preview-heading h2 { margin: 0; font-size: 14px; }
.section-heading > span, .preview-heading p { margin: 0; color: var(--text-muted); font-size: 12px; }
.list-heading, .source-row { display: grid; grid-template-columns: 120px 140px 130px 120px minmax(0, 1fr); gap: 12px; }
.list-heading { padding: 10px 16px; border-bottom: 1px solid var(--border); background: var(--surface-subtle); color: var(--text-muted); font-size: 12px; font-weight: 600; }
.source-row { align-items: center; padding: 12px 16px; border-bottom: 1px solid var(--border); }
.source-row:last-child { border-bottom: 0; }
.agent, .kind, .size { color: var(--text-muted); font-size: 13px; }
.format-tag, .type-tag { display: inline-flex; align-items: center; width: fit-content; padding: 2px 8px; border-radius: 999px; background: var(--brand-soft); color: var(--brand); font-size: 12px; font-weight: 600; }
.type-tag { min-width: 76px; justify-content: center; }
.confidence { color: var(--text-muted); font-size: 12px; font-variant-numeric: tabular-nums; }
.path { display: flex; min-width: 0; align-items: center; gap: 10px; }
.path-text { overflow: hidden; min-width: 0; flex: 1; color: var(--text-muted); font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
.text-button { flex: 0 0 auto; border: 0; background: none; color: var(--brand); cursor: pointer; font-size: 13px; }
.text-button:disabled { color: var(--text-muted); cursor: default; }
.primary-action { margin-left: 8px; color: var(--primary, #4db6ac); font-weight: 600; }
.unsupported-sources { margin-top: 14px; border-top: 1px solid var(--border); border-bottom: 1px solid var(--border); }
.unsupported-sources summary { padding: 12px 16px; cursor: pointer; color: var(--text-muted); font-size: 13px; }
.unsupported-sources > p { margin: 0; padding: 0 16px 10px; color: var(--text-muted); font-size: 12px; }
.unsupported-row { display: grid; grid-template-columns: 120px 140px 150px minmax(0, 1fr); gap: 12px; padding: 10px 16px; border-top: 1px solid var(--border); color: var(--text-muted); font-size: 12px; }
.unsupported-row strong { color: var(--text); font-weight: 500; }
.preview-panel { margin-top: 18px; padding: 0 16px; }
.preview-heading { margin: 0 -16px; }
.candidate-explanation { margin: 12px 0 2px; padding: 10px 12px; border-left: 3px solid var(--brand); background: var(--surface-subtle); color: var(--text-muted); font-size: 12px; line-height: 1.55; }
.preview-source { display: grid; min-width: 0; text-align: right; font-size: 12px; }
.preview-source span { overflow: hidden; max-width: 440px; color: var(--text-muted); text-overflow: ellipsis; white-space: nowrap; }
.candidate-row { display: grid; grid-template-columns: auto 92px minmax(0, 1fr); align-items: start; gap: 10px; padding: 10px 0; border-bottom: 1px solid var(--border); }
.candidate-row:last-of-type { border-bottom: 0; }
.candidate-text p { margin: 0; font-size: 13px; }
.candidate-text .reason, .preview-warning { margin-top: 3px; color: var(--text-muted); font-size: 12px; }
.classification-note { margin-top: 6px !important; padding: 7px 9px; border-left: 2px solid var(--brand); background: var(--brand-soft); color: var(--text-muted); font-size: 12px !important; line-height: 1.5; }
.classification-note strong { color: var(--text); }
.admission-result { margin-top: 6px !important; padding: 7px 9px; border-left: 2px solid var(--warning); background: var(--surface-subtle); color: var(--text-muted); font-size: 12px !important; line-height: 1.5; }
.admission-result strong { color: var(--text); }
.candidate-meta { display: flex; flex-wrap: wrap; gap: 14px; margin: 7px 0 0; color: var(--text-muted); font-size: 12px; }
.candidate-meta div { display: flex; gap: 4px; }.candidate-meta dt { font-weight: 600; }.candidate-meta dd { margin: 0; }
.empty-state { display: flex; min-height: 90px; align-items: center; justify-content: center; gap: 8px; color: var(--text-muted); }
.inline-notice, .inline-error { margin: 0 0 12px; padding: 10px 12px; border-radius: var(--radius); }
.inline-notice { border: 1px solid #b9d8c3; background: var(--success-soft, rgba(77, 182, 172, 0.12)); color: #2e7d5b; }.inline-notice a { margin-left: 8px; color: inherit; font-weight: 600; }
.inline-error { border: 1px solid #efc9c9; background: var(--danger-soft); color: var(--danger); }
.spin { animation: spin 1s linear infinite; }@keyframes spin { to { transform: rotate(360deg); } }
@media (max-width: 760px) { .list-heading { display: none; }.source-row { grid-template-columns: 1fr auto; gap: 6px 10px; }.path { grid-column: 1 / -1; }.unsupported-row { grid-template-columns: 1fr auto; }.unsupported-row .path-text { grid-column: 1 / -1; }.preview-heading { display: grid; }.preview-source { text-align: left; }.preview-source span { max-width: 100%; }.candidate-row { grid-template-columns: auto minmax(0, 1fr); }.candidate-text { grid-column: 1 / -1; } }
</style>
