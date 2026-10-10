<script setup lang="ts">
import { computed, onMounted, ref } from "vue"

import { requireClient, type ActivityItem, type Connector } from "@/api/client"
import ActivityRow from "@/components/ActivityRow.vue"
import { useI18n } from "@/i18n"

const { t } = useI18n()

const activities = ref<ActivityItem[]>([])
const connectors = ref<Connector[]>([])
const loading = ref(true)
const busy = ref<string | null>(null)
const error = ref("")
const kindFilter = ref("")
const onlyMemoryChanges = ref(true)
const visibleCount = ref(30)
const connectorNames = computed(() => new Map(connectors.value.map((connector) => [connector.id, connector.name])))
const kindLabels: Record<string, string> = {
  source_ingested: "来源导入",
  memory_added: "记忆写入",
  memory_revised: "记忆修订",
  memory_deleted: "记忆删除",
  projection_updated: "投影更新",
  operation_undone: "撤销操作",
}
function hasMemoryChanges(activity: ActivityItem): boolean {
  const changes = activity.details.memory_changes
  return Array.isArray(changes) && changes.length > 0
}

const filteredActivities = computed(() => activities.value.filter((activity) => {
  if (kindFilter.value && activity.kind !== kindFilter.value) return false
  if (onlyMemoryChanges.value && activity.kind === "source_ingested" && !hasMemoryChanges(activity)) return false
  return true
}))
const visibleActivities = computed(() => filteredActivities.value.slice(0, visibleCount.value))
const remainingCount = computed(() => Math.max(filteredActivities.value.length - visibleActivities.value.length, 0))

function changeFilter() {
  visibleCount.value = 30
}

function changeVisibility() {
  visibleCount.value = 30
}

async function load(): Promise<boolean> {
  loading.value = true
  error.value = ""
  try {
    const [activityResponse, connectorResponse] = await Promise.all([
      requireClient().activities(),
      requireClient().connectors(),
    ])
    activities.value = activityResponse.items
    connectors.value = connectorResponse.items
    return true
  } catch {
    error.value = t("暂时无法读取活动记录，请稍后重试。")
    return false
  } finally {
    loading.value = false
  }
}

async function undo(activity: ActivityItem) {
  busy.value = activity.operation_id
  error.value = ""
  let undone = false
  try {
    await requireClient().undo(activity.operation_id)
    undone = true
  } catch {
    error.value = t("本次导入无法撤销，相关记忆可能已经发生变化。")
  }
  if (undone && !(await load())) {
    error.value = t("撤销已完成，但刷新活动记录时失败，请稍后重试。")
  }
  busy.value = null
}

onMounted(load)
</script>

<template>
  <section class="page">
    <header><div><h1 class="page-title">{{ t("活动") }}</h1><p class="page-subtitle">{{ t("按一次完整操作追溯来源、结果与可撤销范围。") }}</p></div><a class="command-button" href="#/exceptions">{{ t("查看需处理内容") }}</a></header>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <div class="activity-toolbar">
      <span>{{ t("显示 {visible} / {total} 条活动", { visible: filteredActivities.length, total: activities.length }) }}</span>
      <label class="memory-change-filter"><input v-model="onlyMemoryChanges" type="checkbox" @change="changeVisibility" />{{ t("仅显示实际记忆变化") }}</label>
      <label>{{ t("类型") }}<select v-model="kindFilter" @change="changeFilter"><option value="">{{ t("全部活动") }}</option><option v-for="(label, value) in kindLabels" :key="value" :value="value">{{ t(label) }}</option></select></label>
    </div>
    <div class="panel activity-list" :aria-busy="loading">
      <div v-if="loading" class="state">{{ t("正在读取活动记录...") }}</div>
      <template v-else>
        <ActivityRow
          v-for="activity in visibleActivities"
          :key="activity.id"
          :activity="activity"
          :connector-name="activity.connector_id ? connectorNames.get(activity.connector_id) : undefined"
          :busy="busy === activity.operation_id"
          @undo="undo(activity)"
        />
        <div v-if="!error && !filteredActivities.length" class="state">{{ activities.length && onlyMemoryChanges ? t("没有实际记忆变化；关闭筛选可查看完整来源处理记录。") : (kindFilter ? t("没有符合条件的活动记录。") : t("还没有活动记录。")) }}</div>
        <div v-if="remainingCount" class="load-more"><button class="command-button" type="button" @click="visibleCount += 30">{{ t("再显示 30 条（剩余 {count}）", { count: remainingCount }) }}</button></div>
      </template>
    </div>
  </section>
</template>

<style scoped>
header { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 20px; }
header a { flex: 0 0 auto; text-decoration: none; }
.activity-list { overflow: hidden; }
.activity-toolbar { display: flex; min-height: 46px; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 10px; padding: 0 14px; border: 1px solid var(--border); background: var(--surface-subtle); color: var(--text-muted); font-size: 12px; }
.activity-toolbar label { display: flex; align-items: center; gap: 8px; }
.activity-toolbar .memory-change-filter { margin-left: auto; white-space: nowrap; }
.memory-change-filter input { width: 15px; min-height: 15px; }
.activity-toolbar select { min-height: 32px; padding: 0 28px 0 9px; border: 1px solid var(--border-strong); border-radius: var(--radius); background: var(--surface); color: var(--text); }
.load-more { display: flex; justify-content: center; padding: 14px; border-top: 1px solid var(--border); }
.state { display: grid; min-height: 120px; place-items: center; color: var(--text-muted); }
.error { padding: 10px 12px; border: 1px solid #efc9c9; background: var(--danger-soft); color: var(--danger); }
@media (max-width: 760px) { header { flex-direction: column; } }
</style>
