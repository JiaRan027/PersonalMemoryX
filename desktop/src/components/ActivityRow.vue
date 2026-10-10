<script setup lang="ts">
import { RotateCcw } from "lucide-vue-next"

import type { ActivityItem } from "@/api/client"
import { useI18n } from "@/i18n"
import { formatLocalDateTime } from "@/utils/dateTime"

const props = defineProps<{ activity: ActivityItem; connectorName?: string; busy?: boolean }>()
defineEmits<{ undo: [] }>()
const { t } = useI18n()

const kindLabels: Record<string, string> = {
  source_ingested: "来源导入",
  memory_added: "记忆写入",
  memory_revised: "记忆修订",
  memory_deleted: "记忆删除",
  projection_updated: "投影更新",
  operation_undone: "操作已撤销",
}
const statusLabels: Record<string, string> = {
  completed: "已完成",
  pending: "处理中",
  failed: "失败",
  undone: "已撤销",
}
const detailLabels: Record<string, string> = {
  accepted: "已接纳",
  provisional: "暂定",
  quarantined: "已隔离",
  projected: "已写出",
  deleted: "已删除",
  undone: "已撤销",
  memory_id: "记忆 ID",
  revision: "修订版本",
  source: "写入来源",
  target_operation_id: "目标操作 ID",
  memory_ids: "受影响的记忆",
  source_locator: "来源位置",
  source_name: "来源名称",
  source_profile: "接入方式",
}
const countKeys = ["accepted", "provisional", "quarantined", "projected", "deleted", "undone"]
const typeLabels: Record<string, string> = { fact: "事实", goal: "目标", skill: "能力", preference: "偏好", event: "经历", insight: "洞察", decision: "决策" }
const actionLabels: Record<string, string> = { created: "新增", revised: "纠正", deleted: "删除" }
const generatedSourceNames = new Set(["codex 记忆导入", "claude_code 记忆导入"])

interface MemoryChange { id: string; action: string; type: string; statement: string }

function memoryChanges(): MemoryChange[] {
  const value = props.activity.details.memory_changes
  if (!Array.isArray(value)) return []
  return value.filter((item): item is MemoryChange => Boolean(
    item && typeof item === "object"
    && typeof item.id === "string"
    && typeof item.action === "string"
    && typeof item.type === "string"
    && typeof item.statement === "string",
  ))
}

function labelFor(labels: Record<string, string>, value: string): string {
  return labels[value] ? t(labels[value]) : t("未知（{value}）", { value })
}

function formatValue(value: unknown): string {
  if (Array.isArray(value)) return value.map(String).join("; ") || t("无")
  if (value && typeof value === "object") return JSON.stringify(value)
  return String(value ?? t("无"))
}

function formatDetailValue(key: string, value: unknown): string {
  if (key === "source_name" && typeof value === "string" && generatedSourceNames.has(value)) return t(value)
  return formatValue(value)
}

function sourceName(): string {
  if (props.activity.connector_id) {
    const snapshot = props.activity.details.source_name
    if (typeof snapshot === "string" && snapshot) return generatedSourceNames.has(snapshot) ? t(snapshot) : snapshot
    return props.connectorName ?? t("已移除的来源（记录 {id}）", { id: props.activity.connector_id })
  }
  return typeof props.activity.details.source === "string"
    ? t("Agent（{source}）", { source: props.activity.details.source })
    : t("用户直接操作")
}

function activitySummary(): string {
  if (props.activity.kind === "source_ingested") {
    const count = countKeys.reduce((sum, key) => {
      const value = props.activity.details[key]
      return sum + (typeof value === "number" ? value : 0)
    }, 0)
    if (count > 0) {
      const changed = memoryChanges().length
      return changed > 0
        ? t("处理 {count} 条候选 · 实际变更 {changed} 条记忆", { count, changed })
        : t("处理 {count} 条候选 · 未写入正式记忆", { count })
    }
  }
  return t(props.activity.summary)
}
</script>

<template>
  <article class="activity-row">
    <span class="activity-dot" :class="activity.status" />
    <div class="activity-main">
      <div class="activity-title"><strong>{{ labelFor(kindLabels, activity.kind) }}</strong><span class="status">{{ labelFor(statusLabels, activity.status) }}</span></div>
      <p>{{ activitySummary() }}</p>
      <div class="meta"><span>{{ t("来源：{source}", { source: sourceName() }) }}</span><time :datetime="activity.created_at">{{ formatLocalDateTime(activity.created_at) }}</time></div>
      <div v-if="countKeys.some((key) => activity.details[key] !== undefined)" class="counts">
        <span v-for="key in countKeys.filter((key) => activity.details[key] !== undefined)" :key="key">{{ t(detailLabels[key]) }} {{ activity.details[key] }}</span>
      </div>
      <ul v-if="memoryChanges().length" class="memory-changes" :aria-label="t('本次变更的记忆')">
        <li v-for="change in memoryChanges()" :key="`${change.action}-${change.id}`"><span>{{ t(actionLabels[change.action] ?? "变更") }} · {{ t(typeLabels[change.type] ?? "其他") }}</span><strong>{{ change.statement }}</strong></li>
      </ul>
      <details v-if="Object.keys(activity.details).length" class="details">
        <summary>{{ t("查看审计详情") }}</summary>
        <dl>
          <template v-for="(value, key) in activity.details" :key="key">
            <template v-if="key !== 'memory_changes'">
              <dt>{{ detailLabels[key] ? t(detailLabels[key]) : t("技术字段（{key}）", { key }) }}</dt><dd>{{ formatDetailValue(key, value) }}</dd>
            </template>
          </template>
        </dl>
      </details>
    </div>
    <button v-if="activity.kind === 'source_ingested' && activity.status === 'completed'" class="icon-button" type="button" :aria-label="t('撤销本次导入')" :title="t('撤销本次导入')" :disabled="busy" @click="$emit('undo')"><RotateCcw :size="16" /></button>
  </article>
</template>

<style scoped>
.activity-row { display: grid; grid-template-columns: 10px minmax(0, 1fr) 32px; align-items: start; gap: 12px; padding: 12px 16px; border-bottom: 1px solid var(--border); }
.activity-row:last-child { border-bottom: 0; }
.activity-dot { width: 8px; height: 8px; margin-top: 7px; border-radius: 50%; background: var(--brand); }
.activity-dot.failed { background: var(--danger); }
.activity-main { min-width: 0; }
.activity-title, .meta, .counts { display: flex; flex-wrap: wrap; gap: 8px 12px; }
.activity-title { align-items: center; }
.status { color: var(--text-muted); font-size: 12px; }
.activity-main p { margin: 3px 0 0; color: var(--text-muted); font-size: 13px; }
.meta, .counts { margin-top: 6px; color: var(--text-muted); font-size: 12px; }
.memory-changes { display: grid; gap: 6px; margin: 9px 0 0; padding: 0; list-style: none; }
.memory-changes li { display: grid; grid-template-columns: 92px minmax(0, 1fr); gap: 8px; padding: 7px 9px; background: var(--surface-subtle); font-size: 12px; }
.memory-changes span { color: var(--text-muted); }.memory-changes strong { font-weight: 500; overflow-wrap: anywhere; }
.details { margin-top: 7px; color: var(--text-muted); font-size: 12px; }
summary { cursor: pointer; }
dl { display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: 4px 10px; margin: 7px 0 0; }
dt { color: var(--text); }
dd { min-width: 0; margin: 0; overflow-wrap: anywhere; }
.icon-button { margin-top: 1px; }
@media (max-width: 760px) { .memory-changes li { grid-template-columns: 1fr; gap: 3px; } }
</style>
