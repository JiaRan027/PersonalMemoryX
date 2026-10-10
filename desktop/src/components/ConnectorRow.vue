<script setup lang="ts">
import { Pause, Play, PlugZap, RefreshCw, Trash2, Unplug } from "lucide-vue-next"
import { computed } from "vue"

import type { ActivityItem, Connector } from "@/api/client"
import ServiceStatus from "@/components/ServiceStatus.vue"
import { useI18n } from "@/i18n"
import {
  connectorLocation,
  connectorProfileLabel,
  connectorPurpose,
  connectorSourceRole,
  isImportedSnapshot,
  watchesSourceChanges,
} from "@/connectors/presentation"
import { formatLocalDateTime } from "@/utils/dateTime"

const props = defineProps<{ connector: Connector; latestActivity?: ActivityItem; busy?: boolean }>()
defineEmits<{ sync: []; toggle: []; remove: []; integrate: []; disconnectAgent: [] }>()
const { t } = useI18n()

const sourceRole = computed(() => connectorSourceRole(props.connector))

function numberDetail(key: string): number {
  const value = props.latestActivity?.details?.[key]
  return typeof value === "number" ? value : 0
}

function createdCount(): number {
  const changes = props.latestActivity?.details?.memory_changes
  return Array.isArray(changes)
    ? changes.filter((item) => item && typeof item === "object" && (item as { action?: string }).action === "created").length
    : 0
}

function latestResult(): string {
  if (!props.latestActivity) return t("尚无来源处理记录")
  const reviewed = numberDetail("accepted") + numberDetail("provisional") + numberDetail("quarantined") + numberDetail("restricted") + numberDetail("discarded")
  return t("最近处理 {reviewed} 条候选 · 实际新增 {created} 条 · {time}", {
    reviewed,
    created: createdCount(),
    time: formatLocalDateTime(props.latestActivity.created_at),
  })
}
</script>

<template>
  <div class="connector-row">
    <div class="identity">
      <strong>{{ connector.name }}</strong>
      <span>{{ t(connectorProfileLabel(connector.profile_id)) }} · {{ t(connectorPurpose(connector)) }}</span>
      <span class="source-role" :class="{ warning: sourceRole.warning }">{{ t(sourceRole.label) }}</span>
      <span v-if="sourceRole.warning" class="source-warning">{{ t(sourceRole.warning) }}</span>
      <span class="location" :title="connectorLocation(connector)">{{ connectorLocation(connector) }}</span>
      <span class="namespace">{{ t("范围：{scope}", { scope: connector.namespace }) }}</span>
    </div>
    <ServiceStatus :state="connector.enabled ? connector.status : 'paused'" :snapshot="isImportedSnapshot(connector)" />
    <div class="directions">
      <span v-if="connector.capabilities.read" :class="{ active: connector.read_enabled && connector.enabled }">{{ !connector.read_enabled || !connector.enabled ? t("读取已暂停") : isImportedSnapshot(connector) ? t("导入快照 · 按需重扫") : watchesSourceChanges(connector) ? t("文件变化时自动读取") : t("正在读取") }}</span>
      <span v-if="connector.capabilities.write" :class="{ active: connector.write_enabled }">{{ connector.write_enabled ? t("正在交付") : t("交付已暂停") }}</span>
      <span v-if="!connector.capabilities.read && !connector.capabilities.write">{{ t("仅接收已授权事件") }}</span>
      <small v-if="connector.capabilities.read">{{ latestResult() }}</small>
    </div>
    <div class="row-actions">
      <button v-if="connector.profile_id !== 'claude_code_hook'" class="icon-button" type="button" :aria-label="t('立即同步')" :title="t('立即同步')" :disabled="busy" @click="$emit('sync')">
        <RefreshCw :size="16" />
      </button>
      <button v-if="connector.profile_id === 'claude_code_hook'" class="icon-button" type="button" :aria-label="t('安装 Claude Code 接入')" :title="t('安装或修复 Claude Code 接入')" :disabled="busy" @click="$emit('integrate')">
        <PlugZap :size="16" />
      </button>
      <button v-if="connector.profile_id === 'claude_code_hook'" class="icon-button" type="button" :aria-label="t('移除 Claude Code 接入')" :title="t('只移除 PersonalMemory 项目配置')" :disabled="busy" @click="$emit('disconnectAgent')">
        <Unplug :size="16" />
      </button>
      <button class="icon-button" type="button" :aria-label="connector.enabled ? t('暂停连接') : t('恢复连接')" :title="connector.enabled ? t('暂停连接') : t('恢复连接')" :disabled="busy" @click="$emit('toggle')">
        <Pause v-if="connector.enabled" :size="16" />
        <Play v-else :size="16" />
      </button>
      <button class="icon-button danger" type="button" :aria-label="t('移除连接')" :title="t('移除连接')" :disabled="busy" @click="$emit('remove')">
        <Trash2 :size="16" />
      </button>
    </div>
  </div>
</template>

<style scoped>
.connector-row {
  display: grid;
  min-height: 70px;
  grid-template-columns: minmax(180px, 1fr) 130px 240px auto;
  align-items: center;
  gap: 20px;
  padding: 12px 16px;
  border-bottom: 1px solid var(--border);
}
.connector-row:last-child { border-bottom: 0; }
.identity strong,
.identity span { display: block; }
.identity span { margin-top: 4px; color: var(--text-muted); font-size: 12px; }
.identity .location { overflow: hidden; max-width: 520px; text-overflow: ellipsis; white-space: nowrap; }
.identity .namespace { color: var(--text-subtle, var(--text-muted)); }
.identity .source-role { width: fit-content; padding: 2px 6px; border: 1px solid var(--border); background: var(--surface-subtle); color: var(--text); font-weight: 600; }
.identity .source-role.warning { border-color: #e1c777; background: #fff8dd; color: #705200; }
.identity .source-warning { color: #795f17; }
.directions { display: flex; flex-wrap: wrap; gap: 6px 14px; color: var(--text-muted); font-size: 12px; }
.directions small { flex-basis: 100%; color: var(--text-muted); font-size: 11px; }
.directions .active { color: var(--brand); }
.row-actions { display: flex; gap: 7px; }
.icon-button.danger { color: var(--danger); }
@media (max-width: 760px) {
  .connector-row {
    grid-template-columns: minmax(0, 1fr) auto;
    gap: 10px 14px;
    padding: 14px;
  }
  .directions { grid-column: 1 / -1; flex-wrap: wrap; }
  .row-actions { grid-column: 2; grid-row: 1; }
  .status { grid-column: 1; }
}
</style>
