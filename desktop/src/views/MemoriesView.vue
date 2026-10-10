<script setup lang="ts">
import { Search } from "lucide-vue-next"
import { computed, nextTick, onMounted, ref, watch } from "vue"

import { requireClient, type MemoryItem, type ReviewVerdict } from "@/api/client"
import MemoryDetail from "@/components/MemoryDetail.vue"
import { useI18n } from "@/i18n"
import {
  isDeliveryExcluded,
  isSystemHandled,
  needsMemoryReview,
} from "@/memories/presentation"

const { t } = useI18n()

const memories = ref<MemoryItem[]>([])
const selected = ref<MemoryItem | null>(null)
const search = ref("")
const type = ref("")
const confirmation = ref("attention")
const loading = ref(true)
const error = ref("")
const notice = ref("")
const busy = ref(false)
const detailColumn = ref<HTMLElement | null>(null)

const visible = computed(() => memories.value.filter((memory) => {
  const matchesSearch = !search.value || `${memory.statement} ${memory.reason ?? ""}`.toLocaleLowerCase().includes(search.value.toLocaleLowerCase())
  const matchesConfirmation = !confirmation.value
    || confirmation.value === "all"
    || (confirmation.value === "attention" && needsMemoryReview(memory))
    || (confirmation.value === "handled" && isSystemHandled(memory))
  return matchesSearch && matchesConfirmation && (!type.value || memory.type === type.value)
}))

const typeLabels: Record<string, string> = { fact: "事实", goal: "目标", skill: "能力", preference: "偏好", event: "经历", insight: "洞察", decision: "决策" }
const confirmationLabels: Record<string, string> = { asserted: "已确认", inferred: "系统推断" }
const reviewDecisionLabels: Record<ReviewVerdict, string> = {
  keep: "保留",
  project_experience: "项目经验",
  rewrite: "待提炼",
  source_only: "仅留来源",
  test_artifact: "测试记录",
  needs_context: "证据不足",
}
const pendingCount = computed(() => memories.value.filter(needsMemoryReview).length)
const handledCount = computed(() => memories.value.filter(isSystemHandled).length)
const excludedCount = computed(() => memories.value.filter(isDeliveryExcluded).length)
function memoryStateLabel(memory: MemoryItem): string {
  const triage = memory.review_triage
  if (triage?.requires_user_review) return t("需要你决定")
  if (triage) return triage.delivery_eligible ? t("系统已保留") : t("系统已隔离")
  return t(confirmationLabels[memory.epistemic_state] ?? "待确认")
}

function typeLabel(value: string): string { return typeLabels[value] ? t(typeLabels[value]) : value }

function displayStatement(memory: MemoryItem): string {
  return memory.review_assistance?.summary ?? memory.statement
}

async function load() {
  loading.value = true
  error.value = ""
  const selectedId = selected.value?.id
  try {
    memories.value = (await requireClient().memories()).items
    if (selectedId) selected.value = memories.value.find((memory) => memory.id === selectedId) ?? null
  }
  catch { error.value = t("暂时无法读取记忆。") }
  finally { loading.value = false }
}

async function revise(statement: string, reason: string | null) {
  if (!selected.value) return
  busy.value = true
  error.value = ""
  notice.value = ""
  try {
    selected.value = await requireClient().reviseMemory(selected.value.id, selected.value.revision, statement, reason)
    notice.value = t("已保存第 {revision} 版。", { revision: selected.value.revision })
    await load()
  } catch {
    error.value = t("纠正没有保存。该记忆可能已在别处更新，请刷新后重试。")
  } finally {
    busy.value = false
  }
}

async function review(verdict: ReviewVerdict, scopeHint: string | null) {
  if (!selected.value) return
  busy.value = true
  error.value = ""
  notice.value = ""
  try {
    const memoryId = selected.value.id
    const decision = await requireClient().reviewMemory(memoryId, selected.value.revision, verdict, scopeHint)
    const eligible = verdict === "keep" || verdict === "project_experience"
    const updateDecision = (memory: MemoryItem): MemoryItem => memory.id === memoryId ? {
      ...memory,
      review_decision: decision,
      review_triage: {
        suggested_verdict: verdict,
        effective_verdict: verdict,
        confidence: "high",
        reason_codes: ["USER_OVERRIDE"],
        requires_user_review: false,
        delivery_eligible: eligible,
        graph_eligible: eligible,
        scope_hint: decision.scope_hint,
        overridden_by_user: true,
        memory_revision: memory.revision,
      },
    } : memory
    memories.value = memories.value.map(updateDecision)
    selected.value = updateDecision(selected.value)
    notice.value = t("已记录“{verdict}”，未修改或删除原记忆。", { verdict: t(reviewDecisionLabels[verdict]) })
  } catch {
    error.value = t("审核结论没有保存，原记忆未发生变化。请刷新后重试。")
  } finally {
    busy.value = false
  }
}

async function remove() {
  if (!selected.value || !window.confirm(t("删除后，旧来源和旧导出包都不能恢复这条记忆。确认删除吗？"))) return
  busy.value = true
  error.value = ""
  notice.value = ""
  try {
    await requireClient().deleteMemory(selected.value.id)
    selected.value = null
    notice.value = t("记忆已删除，后续从同一来源导入时不会自动恢复。")
    await load()
  } catch {
    error.value = t("删除没有完成，原记忆仍然保留。请刷新后重试。")
  } finally {
    busy.value = false
  }
}

function applyRouteIntent() {
  const query = window.location.hash.split("?", 2)[1]
  if (!query) return
  const params = new URLSearchParams(query)
  const state = params.get("state")
  confirmation.value = state === "review" || state === "attention"
    ? "attention"
    : state === "handled" || state === "all" ? state : "all"
  const memoryId = params.get("memory")
  if (memoryId) selected.value = memories.value.find((memory) => memory.id === memoryId) ?? null
}

watch([confirmation, type, search], () => {
  if (selected.value && visible.value.some((memory) => memory.id === selected.value?.id)) return
  selected.value = visible.value[0] ?? null
})

watch(() => selected.value?.id, async () => {
  await nextTick()
  if (detailColumn.value) detailColumn.value.scrollTop = 0
})

onMounted(async () => {
  await load()
  applyRouteIntent()
  if (!selected.value && visible.value.length) selected.value = visible.value[0]
})
</script>

<template>
  <section class="page memories-page">
    <header class="page-header"><div><h1 class="page-title">{{ t("连续性治理") }}</h1><p class="page-subtitle">{{ t("系统自动处理大多数记录；这里只需要你决定少量高风险例外。") }}</p></div><a class="command-button" href="#/graph">{{ t("查看关系图") }}</a></header>
    <p v-if="error" class="inline-error" role="alert">{{ error }}</p>
    <p v-if="notice" class="inline-notice" role="status">{{ notice }}</p>
    <div class="memory-workspace panel">
      <div class="memory-list">
        <div class="toolbar">
          <label class="search"><Search :size="16" /><input v-model="search" type="search" :placeholder="t('搜索内容或原因')" /></label>
          <select v-model="type" :aria-label="t('记忆类型')">
            <option value="">{{ t("全部类型") }}</option><option v-for="(label, value) in typeLabels" :key="value" :value="value">{{ t(label) }}</option>
          </select>
          <select v-model="confirmation" :aria-label="t('治理状态')">
            <option value="attention">{{ t("需要你决定") }}</option><option value="handled">{{ t("系统已处理") }}</option><option value="all">{{ t("全部记录") }}</option>
          </select>
        </div>
        <div class="memory-summary" :aria-label="t('治理统计')">
          <span>{{ t("全部 {count}", { count: memories.length }) }}</span><span>{{ t("需要你决定 {count}", { count: pendingCount }) }}</span><span>{{ t("系统已处理 {count}", { count: handledCount }) }}</span><span>{{ t("不交付给 Agent {count}", { count: excludedCount }) }}</span>
        </div>
        <div v-if="confirmation === 'attention'" class="review-guide" role="note">
          <strong>{{ pendingCount ? t("只剩 {count} 条需要你的判断", { count: pendingCount }) : t("系统已处理完当前记录") }}</strong>
          <span>{{ pendingCount ? t("这里只保留敏感、冲突或高影响推断，不要求你审核普通日志。") : t("之后只有系统无法安全决定时才会出现在这里。") }}</span>
        </div>
        <div class="memory-list-scroll" data-scroll-region="memory-list" :aria-label="t('记忆列表')" tabindex="0">
          <div v-if="loading" class="empty">{{ t("正在读取记忆...") }}</div>
          <div class="table-heading"><span>{{ t("类型") }}</span><span>{{ t("内容") }}</span><span>{{ t("状态") }}</span><span>{{ t("版本") }}</span></div>
          <button v-for="memory in visible" :key="memory.id" class="memory-row" type="button" :data-memory-id="memory.id" :class="{ selected: selected?.id === memory.id }" @click="selected = memory">
            <span class="memory-type">{{ typeLabel(memory.type) }}</span>
            <span class="memory-statement" :title="memory.review_assistance ? t('英文原文：{statement}', { statement: memory.statement }) : memory.statement">
              <span>{{ displayStatement(memory) }}</span>
            </span>
            <span :class="{ warning: needsMemoryReview(memory) }">{{ memoryStateLabel(memory) }}</span>
            <span>v{{ memory.revision }}</span>
          </button>
          <div v-if="!loading && !visible.length" class="empty">{{ confirmation === "attention" ? t("当前没有需要你决定的记忆。") : t("没有符合条件的记录。") }}</div>
        </div>
      </div>
      <div ref="detailColumn" class="memory-detail-column" data-scroll-region="memory-detail" :aria-label="t('记忆详情')" tabindex="0">
        <MemoryDetail v-if="selected" :memory="selected" :busy="busy" @close="selected = null" @revise="revise" @delete="remove" @review="review" />
        <div v-else class="detail-empty">{{ t("选择一条记忆查看来源、原因和版本。") }}</div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.page-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 20px; }
.memories-page { display: flex; min-height: 0; flex: 1; flex-direction: column; overflow: hidden; }
.page-header a { flex: 0 0 auto; text-decoration: none; }
.inline-notice, .inline-error { margin: 0 0 12px; padding: 10px 12px; border-radius: var(--radius); }
.inline-notice { border: 1px solid #b9d8c3; background: var(--success-soft, rgba(77, 182, 172, 0.12)); color: #2e7d5b; }
.inline-error { border: 1px solid #efc9c9; background: var(--danger-soft); color: var(--danger); }
.memory-workspace { display: grid; min-height: 0; flex: 1; grid-template-columns: minmax(520px, 1fr) 340px; overflow: hidden; }
.memory-list { display: flex; min-width: 0; min-height: 0; flex-direction: column; overflow: hidden; }
.memory-list-scroll,
.memory-detail-column { min-height: 0; overflow-y: auto; overscroll-behavior: contain; scrollbar-gutter: stable; }
.memory-list-scroll { flex: 1; }
.memory-detail-column { border-left: 1px solid var(--border); background: var(--surface); }
.memory-detail-column:focus-visible,
.memory-list-scroll:focus-visible { outline: 2px solid var(--focus); outline-offset: -2px; }
.memory-detail-column :deep(.detail) { position: static; min-height: 100%; border-left: 0; }
.toolbar { display: flex; gap: 10px; padding: 14px 16px; border-bottom: 1px solid var(--border); }
.memory-summary { display: flex; gap: 14px; padding: 9px 16px; border-bottom: 1px solid var(--border); background: var(--surface-subtle); color: var(--text-muted); font-size: 12px; }
.review-guide { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 12px; padding: 10px 16px; border-bottom: 1px solid #b9d8c3; background: var(--success-soft, rgba(77, 182, 172, 0.12)); color: var(--text-muted); font-size: 12px; }
.review-guide strong { color: var(--text); }
.search { display: flex; min-height: 36px; flex: 1; align-items: center; gap: 8px; padding: 0 10px; border: 1px solid var(--border-strong); border-radius: var(--radius); color: var(--text-muted); }
.search input { width: 100%; border: 0; outline: 0; }
select { min-width: 130px; padding: 0 9px; border: 1px solid var(--border-strong); border-radius: var(--radius); background: var(--surface); }
.table-heading,
.memory-row { display: grid; grid-template-columns: 70px minmax(240px, 1fr) 80px 48px; gap: 12px; align-items: center; text-align: left; }
.table-heading { position: sticky; z-index: 1; top: 0; padding: 9px 16px; border-bottom: 1px solid var(--border); background: var(--surface-subtle); color: var(--text-muted); font-size: 12px; }
.memory-row { width: 100%; min-height: 58px; padding: 9px 16px; border: 0; border-bottom: 1px solid var(--border); background: var(--surface); color: var(--text-muted); }
.memory-row:hover,
.memory-row.selected { background: var(--brand-soft); }
.memory-statement { display: flex; min-width: 0; align-items: center; gap: 6px; overflow: hidden; color: var(--text); white-space: nowrap; }
.memory-statement > span { min-width: 0; overflow: hidden; text-overflow: ellipsis; }
.memory-statement small { flex: none; padding: 2px 4px; border: 1px solid var(--border); border-radius: 3px; color: var(--text-muted); font-size: 9px; font-weight: 500; }
.memory-type { color: var(--brand); font-size: 12px; font-weight: 600; }
.warning { color: var(--warning); font-weight: 600; }
.empty,
.detail-empty { display: grid; min-height: 180px; place-items: center; padding: 24px; color: var(--text-muted); text-align: center; }
.detail-empty { min-height: 100%; }
@media (max-width: 760px) {
  .memories-page { display: block; overflow: visible; }
  .page-header { flex-direction: column; }
  .memory-workspace { display: grid; min-height: 560px; grid-template-columns: minmax(0, 1fr); overflow: visible; }
  .memory-list { overflow: visible; }
  .memory-list-scroll,
  .memory-detail-column { overflow: visible; }
  .memory-detail-column { border-top: 1px solid var(--border); border-left: 0; }
  .memory-detail-column :deep(.detail) { min-height: 0; }
  .toolbar { flex-wrap: wrap; }
  .search { flex-basis: 100%; }
  select { min-height: 36px; }
  .table-heading,
  .memory-row { grid-template-columns: 56px minmax(0, 1fr); }
  .table-heading > span:nth-child(n + 3),
  .memory-row > span:nth-child(n + 3) { display: none; }
  .detail-empty { display: none; }
}
</style>
