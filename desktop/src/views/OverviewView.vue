<script setup lang="ts">
import { ArrowRight, Brain, CircleAlert, CircleHelp, FolderSync, Link2, RefreshCw } from "lucide-vue-next"
import { computed, onMounted, ref } from "vue"
import { RouterLink } from "vue-router"

import { requireClient, type ActivityItem, type Connector, type ExceptionItem, type MemoryItem } from "@/api/client"
import { isContinuousConnector } from "@/connectors/presentation"
import { isDeliveryExcluded, isSystemHandled, needsMemoryReview } from "@/memories/presentation"
import { formatLocalDateTime } from "@/utils/dateTime"
import { useI18n } from "@/i18n"

const { t } = useI18n()

const connectors = ref<Connector[]>([])
const memories = ref<MemoryItem[]>([])
const activities = ref<ActivityItem[]>([])
const exceptions = ref<ExceptionItem[]>([])
const unavailable = ref(false)
const serviceVersion = ref("")
const buildRevision = ref("")
const reviewMemories = computed(() => memories.value.filter(needsMemoryReview))
const reviewCount = computed(() => reviewMemories.value.length)
const handledCount = computed(() => memories.value.filter(isSystemHandled).length)
const excludedCount = computed(() => memories.value.filter(isDeliveryExcluded).length)
const continuousCount = computed(() => connectors.value.filter(isContinuousConnector).length)
const recentMemories = computed(() => {
  const deliverable = memories.value.filter((memory) => memory.review_triage?.delivery_eligible !== false)
  const localized = deliverable.filter((memory) => memory.review_assistance || /[\u3400-\u9fff]/u.test(memory.statement))
  const fallback = deliverable.filter((memory) => !localized.includes(memory))
  return [...localized, ...fallback].slice(0, 4)
})
const recentActivities = computed(() => {
  const seen = new Set<string>()
  return activities.value.filter((activity) => {
    const key = `${activity.kind}:${activity.summary}`
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
})
const typeLabels: Record<string, string> = { fact: "事实", goal: "目标", skill: "能力", preference: "偏好", event: "经历", insight: "洞察", decision: "决策" }
function typeLabel(type: string): string { return typeLabels[type] ? t(typeLabels[type]) : type }
function memoryStatement(memory: MemoryItem): string { return memory.review_assistance?.summary ?? memory.statement }

async function load() {
  try {
    const client = requireClient()
    const [health, connectionData, memoryData, activityData, exceptionData] = await Promise.all([
      client.health(), client.connectors(), client.memories(), client.activities(), client.exceptions(),
    ])
    serviceVersion.value = health.version
    buildRevision.value = health.build_revision
    connectors.value = connectionData.items
    memories.value = memoryData.items
    activities.value = activityData.items.slice(0, 4)
    exceptions.value = exceptionData.items.slice(0, 3)
    unavailable.value = false
  } catch { unavailable.value = true }
}

onMounted(load)
</script>

<template>
  <section class="page overview">
    <header><div><h1 class="page-title">{{ t("连续性总览") }}</h1><p class="page-subtitle">{{ t("你的长期记忆、Agent 接入与近期治理状态。") }}</p></div><button class="icon-button" type="button" :aria-label="t('刷新首页')" :title="t('刷新首页')" @click="load"><RefreshCw :size="17" /></button></header>
    <section class="health-band" :class="{ unavailable }">
      <div class="health-icon"><CircleAlert v-if="unavailable" :size="22" /><Link2 v-else :size="22" /></div>
      <div><strong>{{ unavailable ? t("等待服务恢复") : `PersonalMemory v${serviceVersion} · ${buildRevision} · Continuity Core` }}</strong><p>{{ unavailable ? t("已保存的数据不会受影响。") : t("{count} 条长期记忆 · {connections} 个持续接入 · 数据仅保存在本机", { count: memories.length, connections: continuousCount }) }}</p></div>
    </section>
    <nav class="metrics" :aria-label="t('连续性总览')">
      <RouterLink to="/memories"><Brain :size="18" /><span><strong>{{ memories.length }}</strong><small>{{ t("全部记忆") }}</small></span></RouterLink>
      <RouterLink :to="{ path: '/memories', query: { state: 'attention' } }" :class="{ attention: reviewCount }"><CircleHelp :size="18" /><span><strong>{{ reviewCount }}</strong><small>{{ t("需要你决定") }}</small></span></RouterLink>
      <RouterLink :to="{ path: '/memories', query: { state: 'handled' } }"><Link2 :size="18" /><span><strong>{{ handledCount }}</strong><small>{{ t("系统已处理") }}</small></span></RouterLink>
      <RouterLink :to="{ path: '/memories', query: { state: 'handled' } }"><CircleAlert :size="18" /><span><strong>{{ excludedCount }}</strong><small>{{ t("不交付给 Agent") }}</small></span></RouterLink>
      <RouterLink to="/connections"><FolderSync :size="18" /><span><strong>{{ continuousCount }}</strong><small>{{ t("Agent 接入") }}</small></span></RouterLink>
    </nav>
    <div class="overview-grid">
      <section>
        <div class="section-heading"><h2>{{ t("最近可供 Agent 使用") }}</h2><RouterLink to="/memories">{{ t("检查全部") }}<ArrowRight :size="14" /></RouterLink></div>
        <div class="panel compact-list">
          <RouterLink v-for="memory in recentMemories" :key="memory.id" class="compact-row memory-link" :to="{ path: '/memories', query: { memory: memory.id } }"><span class="memory-copy"><small>{{ typeLabel(memory.type) }}</small><span class="memory-statement" :title="memory.statement">{{ memoryStatement(memory) }}</span></span><span class="memory-status">{{ t("可供 Agent 使用") }}</span></RouterLink>
          <div v-if="!recentMemories.length" class="empty">{{ t("还没有长期记忆。") }}</div>
        </div>
      </section>
      <section>
        <div class="section-heading"><h2>{{ t("需要处理") }}</h2><RouterLink to="/exceptions">{{ t("查看全部") }}<ArrowRight :size="14" /></RouterLink></div>
        <div class="panel compact-list">
          <RouterLink v-for="memory in reviewMemories.slice(0, 2)" :key="memory.id" class="compact-row exception" :to="{ path: '/memories', query: { state: 'attention', memory: memory.id } }"><span class="exception-copy" :title="memory.statement">{{ memory.statement }}</span><span class="exception-status">{{ t("需要你决定") }}</span></RouterLink>
          <div v-for="item in exceptions.slice(0, 1)" :key="item.id" class="compact-row exception"><span class="exception-copy" :title="item.statement || t('来源或候选需要检查')">{{ item.statement || t("来源或候选需要检查") }}</span><span class="exception-status">{{ t("未进入记忆库") }}</span></div>
          <div v-if="!reviewMemories.length && !exceptions.length" class="empty">{{ t("没有冲突或隔离候选。") }}</div>
        </div>
      </section>
    </div>
    <section class="recent">
      <div class="section-heading"><h2>{{ t("最近活动") }}</h2><RouterLink to="/activity">{{ t("全部") }}<ArrowRight :size="14" /></RouterLink></div>
      <div class="panel compact-list"><div v-for="activity in recentActivities" :key="activity.id" class="compact-row"><span>{{ activity.summary }}</span><time :datetime="activity.created_at">{{ formatLocalDateTime(activity.created_at) }}</time></div><div v-if="!recentActivities.length" class="empty">{{ t("还没有活动记录。") }}</div></div>
    </section>
  </section>
</template>

<style scoped>
.overview > header { display: flex; align-items: flex-start; justify-content: space-between; }
.health-band { display: flex; align-items: center; gap: 14px; margin: 22px 0 28px; padding: 18px 20px; border: 1px solid #b9d8cf; background: var(--brand-soft); color: var(--brand); }
.health-band.unavailable { border-color: #efc9c9; background: var(--danger-soft); color: var(--danger); }
.health-icon { display: grid; width: 38px; height: 38px; place-items: center; border: 1px solid currentColor; border-radius: 50%; }
.health-band strong { font-size: 16px; }.health-band p { margin: 4px 0 0; }
.metrics { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 1px; margin: 0 0 28px; border: 1px solid var(--border); background: var(--border); }
.metrics a { display: flex; min-height: 74px; align-items: center; gap: 12px; padding: 12px 16px; background: var(--surface); color: var(--text); text-decoration: none; }
.metrics a:hover { background: var(--surface-subtle); }
.metrics a.attention { color: var(--warning); }
.metrics span,.metrics small { display: block; }
.metrics strong { font-size: 20px; }
.metrics small { margin-top: 2px; color: var(--text-muted); }
.overview-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }
.section-heading { display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; }
.section-heading h2 { margin: 0; font-size: 16px; }
.section-heading a { display: inline-flex; align-items: center; gap: 4px; color: var(--brand); text-decoration: none; }
.compact-list { overflow: hidden; }
.compact-row { display: flex; min-height: 50px; align-items: center; justify-content: space-between; gap: 16px; padding: 10px 14px; border-bottom: 1px solid var(--border); }
.memory-link { min-width: 0; overflow: hidden; color: var(--text); text-decoration: none; }
.memory-copy { display: grid; min-width: 0; flex: 1 1 auto; gap: 3px; overflow: hidden; }
.memory-statement { display: block; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.memory-link small { color: var(--brand); font-size: 11px; font-weight: 600; }
.memory-status { flex: 0 0 auto; color: var(--text-muted); font-size: 12px; white-space: nowrap; }
.compact-row:last-child { border-bottom: 0; }
.compact-row time,.exception-status { flex: 0 0 auto; color: var(--text-muted); font-size: 12px; white-space: nowrap; }
.exception { min-width: 0; overflow: hidden; color: var(--text); text-decoration: none; }
.exception-copy { display: -webkit-box; min-width: 0; overflow: hidden; -webkit-box-orient: vertical; -webkit-line-clamp: 2; line-height: 1.45; overflow-wrap: anywhere; }
.recent { margin-top: 26px; }.empty { display: grid; min-height: 86px; place-items: center; color: var(--text-muted); }
@media (max-width: 760px) {
  .overview-grid { grid-template-columns: minmax(0, 1fr); }
  .metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .health-band { align-items: flex-start; padding: 14px; }
  .compact-row { align-items: flex-start; }
}
</style>
