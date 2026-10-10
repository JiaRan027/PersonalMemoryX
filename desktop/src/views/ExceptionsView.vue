<script setup lang="ts">
import { CircleAlert, Shield } from "lucide-vue-next"
import { onMounted, ref } from "vue"

import { requireClient, type ExceptionItem } from "@/api/client"
import { useI18n } from "@/i18n"

const { t } = useI18n()

const items = ref<ExceptionItem[]>([])
const error = ref("")
const loading = ref(true)

const connectorMessages: Record<string, string> = {
  PROJECTION_CONFLICT: "目标文件已被外部修改，PersonalMemory 已停止写入",
  MANAGED_BLOCK_MISSING: "目标文件的受管区域已丢失，PersonalMemory 已停止写入",
  PROJECTION_VERIFICATION_FAILED: "写入后的文件校验失败，原文件已恢复",
  FILE_ACCESS_DENIED: "文件权限发生变化，请检查授权",
  ENCODING_ERROR: "文件编码无法安全读取",
  FILE_IO_ERROR: "来源文件暂时不可访问或已经移动；已导入的记忆不会删除。如果不再使用该来源，可在“来源与接入”中暂停它。",
  CONNECTOR_CONFIGURATION_ERROR: "连接配置已经失效，请重新授权",
  PROJECTION_FAILED: "写出失败，请检查连接后重试",
  SOURCE_FAILED: "读取失败，请检查连接后重试",
}

function exceptionTitle(item: ExceptionItem): string {
  if (item.kind === "memory_quality" || item.reason_code === "CROSS_NAMESPACE_EXACT_DUPLICATE") return t("记忆在不同适用范围中重复")
  return item.kind === "quarantine" ? t("记忆内容存在冲突") : item.statement || t("连接需要处理")
}

function exceptionMessage(item: ExceptionItem): string {
  if (item.kind === "memory_quality" || item.reason_code === "CROSS_NAMESPACE_EXACT_DUPLICATE") {
    const namespaces = item.related_namespaces?.filter(Boolean).join("、")
    return namespaces ? t("这条内容在“{namespaces}”中完全重复。请打开记忆详情，确认应保留的适用范围。", { namespaces }) : t("这条内容在不同适用范围中完全重复。请打开记忆详情核对。")
  }
  if (item.kind === "quarantine") return item.statement || t("请检查这条记忆")
  return t(connectorMessages[item.reason_code] || "请检查连接路径和权限")
}

async function load() {
  loading.value = true
  error.value = ""
  items.value = []
  try { items.value = (await requireClient().exceptions()).items }
  catch { error.value = t("暂时无法读取异常。") }
  loading.value = false
}

onMounted(() => { void load() })
</script>

<template>
  <section class="page">
    <header><h1 class="page-title">{{ t("需要处理") }}</h1><p class="page-subtitle">{{ t("这里只列出被隔离的记忆和当前异常的来源。连接异常不会删除已保存的记忆；只有仍需要该来源持续同步时，才需要修复它。不再使用的导入来源可以在“来源与接入”中暂停。") }}</p></header>
    <div v-if="loading" class="panel exception-list status-state" role="status">{{ t("正在读取异常...") }}</div>
    <div v-else-if="error" class="panel exception-list status-state">
      <p class="error" role="alert">{{ error }}</p>
      <button class="command-button" type="button" @click="load">{{ t("重试") }}</button>
    </div>
    <div v-else class="panel exception-list">
      <article v-for="item in items" :key="item.id">
        <CircleAlert :size="18" />
        <div><strong>{{ exceptionTitle(item) }}</strong><p>{{ exceptionMessage(item) }}</p></div>
        <a v-if="item.memory_id" class="memory-link" :href="`#/memories?memory=${encodeURIComponent(item.memory_id)}`">{{ t("查看记忆") }}</a><span v-else>{{ item.memory_type || t("连接") }}</span>
      </article>
      <div v-if="!items.length" class="empty"><Shield :size="24" /><strong>{{ t("没有需要处理的异常") }}</strong><span>{{ t("普通同步和暂定记忆不会在这里打扰你。") }}</span></div>
    </div>
  </section>
</template>

<style scoped>
header { margin-bottom: 20px; }.error { color: var(--danger); }.exception-list { overflow: hidden; }
.status-state { display: grid; min-height: 180px; place-items: center; align-content: center; gap: 12px; color: var(--text-muted); }
article { display: grid; min-height: 76px; grid-template-columns: 22px minmax(0,1fr) auto; align-items: center; gap: 12px; padding: 12px 16px; border-bottom: 1px solid var(--border); color: var(--warning); }
article strong { color: var(--text); } article p { margin: 5px 0 0; color: var(--text-muted); } article > span { color: var(--text-muted); font-size: 12px; }
.memory-link { color: var(--brand); font-size: 12px; font-weight: 600; text-decoration: none; white-space: nowrap; }
.empty { display: grid; min-height: 260px; place-items: center; align-content: center; gap: 8px; color: var(--brand); }.empty span { color: var(--text-muted); }
</style>
