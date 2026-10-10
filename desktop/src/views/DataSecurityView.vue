<script setup lang="ts">
import { open, save } from "@tauri-apps/plugin-dialog"
import { Check, Clock3, Copy, Download, FolderLock, PackageOpen, SearchCheck, ShieldCheck, Upload } from "lucide-vue-next"
import { computed, onMounted, ref } from "vue"

import {
  ApiRequestError,
  requireClient,
  type CapsuleResponse,
  type CapsuleValidationResult,
  type MemoryNamespaceSummary,
} from "@/api/client"
import { formatLocalDateTime } from "@/utils/dateTime"
import { useI18n } from "@/i18n"

const { t } = useI18n()

const message = ref("")
const error = ref("")
const exportBusy = ref(false)
const importBusy = ref(false)
const capsuleBusy = ref(false)
const capsuleQuery = ref("")
const namespaceOptions = ref<MemoryNamespaceSummary[]>([])
const namespaceLoading = ref(false)
const namespaceLoadFailed = ref(false)
const capsuleScopeMode = ref<"all" | "selected">("all")
const selectedNamespaces = ref<string[]>([])
const currentTask = ref("")
const verifiedStatus = ref("")
const nextAction = ref("")
const capsuleTtl = ref(3600)
const capsuleBudget = ref(1200)
const capsuleResult = ref<CapsuleResponse | null>(null)
const capsuleCopied = ref(false)
const capsuleValidation = ref<CapsuleValidationResult | null>(null)
const capsuleValidationBusy = ref(false)
const capsuleRebuildBusy = ref(false)
const capsuleExportBusy = ref(false)
const capsuleScopeLabel = computed(() => {
  if (capsuleScopeMode.value === "all") {
    const count = namespaceOptions.value.reduce((sum, item) => sum + item.memory_count, 0)
    return count ? t("全部范围（{count} 条）", { count }) : t("全部可用范围")
  }
  return selectedNamespaces.value.length === 1
    ? selectedNamespaces.value[0]
    : t("已选 {count} 个范围", { count: selectedNamespaces.value.length })
})

async function loadNamespaces() {
  namespaceLoading.value = true
  namespaceLoadFailed.value = false
  try {
    const response = await requireClient().memoryNamespaces()
    namespaceOptions.value = Array.isArray(response.items) ? response.items : []
  } catch {
    namespaceLoadFailed.value = true
  } finally {
    namespaceLoading.value = false
  }
}

function useAllNamespaces() {
  capsuleScopeMode.value = "all"
  selectedNamespaces.value = []
}

function toggleNamespace(namespace: string, checked: boolean) {
  capsuleScopeMode.value = "selected"
  selectedNamespaces.value = checked
    ? [...new Set([...selectedNamespaces.value, namespace])]
    : selectedNamespaces.value.filter((item) => item !== namespace)
  if (!selectedNamespaces.value.length) useAllNamespaces()
}

function timestampedFilename(prefix: string, extension: string): string {
  const now = new Date()
  const pad = (value: number) => String(value).padStart(2, "0")
  const timestamp = `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`
  return `${prefix}-${timestamp}.${extension}`
}

function ensureExtension(path: string, extension: string): string {
  return path.toLocaleLowerCase().endsWith(`.${extension}`) ? path : `${path}.${extension}`
}

async function exportData() {
  exportBusy.value = true
  message.value = ""
  error.value = ""
  try {
    const selectedPath = await save({
      title: t("导出完整记忆"),
      defaultPath: timestampedFilename("personalmemory", "pmemory"),
      filters: [{ name: t("PersonalMemory 迁移包"), extensions: ["pmemory"] }],
    })
    if (!selectedPath) return
    const report = await requireClient().exportPackage(ensureExtension(selectedPath, "pmemory"))
    message.value = t("已导出 {count} 条记忆。迁移包：{package}；可阅读副本：{readable}", {
      count: report.memory_count ?? t("全部"),
      package: report.package_path,
      readable: report.readable_path,
    })
  }
  catch { error.value = "导出没有完成，请检查保存位置。" }
  finally { exportBusy.value = false }
}

async function inspectAndImport() {
  importBusy.value = true
  message.value = ""
  error.value = ""
  try {
    const selectedPath = await open({
      title: t("选择要导入的记忆备份"),
      multiple: false,
      directory: false,
      filters: [{ name: t("PersonalMemory 迁移包"), extensions: ["pmemory"] }],
    })
    if (!selectedPath) return
    const report = await requireClient().inspectPackage(selectedPath)
    if (!report.valid) {
      error.value = "这个导入包未通过完整性验证，未写入任何数据。"
      return
    }
    if (!window.confirm(t("这个备份包含 {count} 条记忆。确认导入吗？", { count: report.memory_count }))) return
    const result = await requireClient().importPackage(selectedPath)
    message.value = t("已导入 {imported} 条，跳过 {skipped} 条。", {
      imported: result.imported,
      skipped: result.skipped_tombstoned + result.skipped_existing,
    })
  } catch (caught) {
    if (caught instanceof ApiRequestError && caught.code === "PACKAGE_NOT_FOUND") {
      error.value = "所选备份已经不存在，请重新选择文件。未写入任何数据。"
    } else if (caught instanceof ApiRequestError && caught.code === "PACKAGE_VERSION_UNSUPPORTED") {
      error.value = "这个备份使用当前版本不支持的迁移协议，请先升级 PersonalMemory。未写入任何数据。"
    } else if (caught instanceof ApiRequestError && caught.code === "PACKAGE_INVALID") {
      error.value = "这个备份未通过完整性校验，文件可能已损坏。未写入任何数据。"
    } else {
      error.value = "无法连接本机服务或读取所选备份。未写入任何数据。"
    }
  }
  finally { importBusy.value = false }
}

function optionalText(value: string): string | null {
  return value.trim() || null
}

async function buildCapsule() {
  capsuleBusy.value = true
  capsuleResult.value = null
  capsuleCopied.value = false
  capsuleValidation.value = null
  message.value = ""
  error.value = ""
  try {
    capsuleResult.value = await requireClient().buildCapsule({
      query: capsuleQuery.value.trim(),
      namespaces: capsuleScopeMode.value === "selected" ? selectedNamespaces.value : null,
      checkpoint: {
        current_task: optionalText(currentTask.value),
        verified_status: optionalText(verifiedStatus.value),
        next_action: optionalText(nextAction.value),
      },
      ttl_seconds: capsuleTtl.value,
      token_budget: capsuleBudget.value,
    })
    await validateCurrentCapsule()
  } catch {
    error.value = "连续性芯片没有生成。请缩小范围，或移除可能包含敏感凭据的工作状态。"
  } finally {
    capsuleBusy.value = false
  }
}

async function validateCurrentCapsule() {
  if (!capsuleResult.value) return
  capsuleValidationBusy.value = true
  try {
    const result = await requireClient().validateCapsule(capsuleResult.value.capsule)
    capsuleValidation.value = Array.isArray(result?.issues) ? result : null
  } catch {
    capsuleValidation.value = null
    error.value = "无法验证当前 Capsule。"
  } finally {
    capsuleValidationBusy.value = false
  }
}

async function rebuildCurrentCapsule() {
  if (!capsuleResult.value) return
  capsuleRebuildBusy.value = true
  capsuleValidation.value = null
  message.value = ""
  error.value = ""
  try {
    await validateCurrentCapsule()
    capsuleResult.value = await requireClient().rebuildCapsule(
      capsuleResult.value.capsule,
      capsuleTtl.value,
    )
    await validateCurrentCapsule()
    message.value = "已按当前 Memory Core 重建 Capsule，并刷新有效期。"
  } catch {
    error.value = "Capsule 重建没有完成。"
  } finally {
    capsuleRebuildBusy.value = false
  }
}

async function exportCapsuleFile() {
  if (!capsuleResult.value) return
  capsuleExportBusy.value = true
  message.value = ""
  error.value = ""
  try {
    const selectedPath = await save({
      title: t("保存 Continuity Capsule"),
      defaultPath: timestampedFilename("personalmemory-capsule", "md"),
      filters: [{ name: t("Markdown 文件"), extensions: ["md"] }],
    })
    if (!selectedPath) return
    const result = await requireClient().exportCapsule(
      capsuleResult.value.capsule,
      ensureExtension(selectedPath, "md"),
    )
    message.value = t("已保存 Capsule：{path}", { path: result.path })
  } catch {
    error.value = "Capsule 文件没有保存。请检查路径，或先验证并重建当前 Capsule。"
  } finally {
    capsuleExportBusy.value = false
  }
}

async function copyCapsule() {
  if (!capsuleResult.value) return
  try {
    await navigator.clipboard.writeText(capsuleResult.value.delivery_text)
    capsuleCopied.value = true
    window.setTimeout(() => { capsuleCopied.value = false }, 1800)
  } catch {
    error.value = "复制失败，请检查桌面端剪贴板权限。"
  }
}

onMounted(loadNamespaces)
</script>

<template>
  <section class="page">
    <header><h1 class="page-title">{{ t("数据与安全") }}</h1><p class="page-subtitle">{{ t("你的记忆属于你，可以随时带走。") }}</p></header>
    <div class="security-band"><FolderLock :size="22" /><div><strong>{{ t("数据保存在这台电脑") }}</strong><span>%LOCALAPPDATA%\PersonalMemory</span></div></div>
    <p v-if="message" class="message" role="status">{{ t(message) }}</p><p v-if="error" class="error" role="alert">{{ t(error) }}</p>
    <div class="data-sections">
      <section><div class="section-icon"><Download :size="20" /></div><div><h2>{{ t("导出完整记忆") }}</h2><p>{{ t("点击导出后选择保存位置。系统会同时生成可恢复的 .pmemory 迁移包和可直接阅读的 Markdown 副本。") }}</p><button class="command-button" type="button" data-package-export :disabled="exportBusy || importBusy" @click="exportData"><Download :size="16" />{{ exportBusy ? t("正在导出...") : t("选择位置并导出") }}</button></div></section>
      <section><div class="section-icon"><Upload :size="20" /></div><div><h2>{{ t("检查并导入") }}</h2><p>{{ t("选择 .pmemory 备份后，系统会先验证文件完整性，并保留已经删除的记忆状态。") }}</p><button class="command-button" type="button" data-package-import :disabled="importBusy || exportBusy" @click="inspectAndImport"><SearchCheck :size="16" />{{ importBusy ? t("正在检查并导入...") : t("选择备份并导入") }}</button></div></section>
    </div>
    <section class="continuity-section" aria-labelledby="continuity-title">
      <div class="continuity-heading">
        <div class="section-icon"><PackageOpen :size="20" /></div>
        <div><h2 id="continuity-title">{{ t("交给新的 Agent") }}</h2><p>{{ t("生成一份短期 Continuity Capsule；Memory Core 仍是唯一真相源。") }}</p></div>
      </div>
      <div class="capsule-form">
        <label class="wide">{{ t("当前任务或问题") }}<input v-model="capsuleQuery" data-capsule-query :placeholder="t('例如：继续 PersonalMemory 的来源治理')" /></label>
        <div class="scope-field">
          <span class="field-label">{{ t("记忆范围") }}</span>
          <details class="scope-picker" data-scope-picker>
            <summary>{{ namespaceLoading ? t("正在读取范围...") : capsuleScopeLabel }}</summary>
            <div class="scope-options">
              <label class="scope-option">
                <input type="radio" name="capsule-scope" :checked="capsuleScopeMode === 'all'" @change="useAllNamespaces" />
                <span><strong>{{ t("全部可用范围") }}</strong><small>{{ t("系统自动选择与当前问题相关的记忆") }}</small></span>
              </label>
              <label v-for="item in namespaceOptions" :key="item.namespace" class="scope-option">
                <input type="checkbox" :checked="selectedNamespaces.includes(item.namespace)" :data-namespace="item.namespace" @change="toggleNamespace(item.namespace, ($event.target as HTMLInputElement).checked)" />
                <span><strong>{{ item.namespace }}</strong><small>{{ t("{count} 条记忆", { count: item.memory_count }) }}</small></span>
              </label>
              <p v-if="!namespaceLoading && !namespaceOptions.length" class="scope-empty">{{ t("还没有可选择的现有范围，将使用全部可用记忆。") }}</p>
            </div>
          </details>
          <small v-if="namespaceLoadFailed" class="scope-load-error">{{ t("范围列表暂时无法读取，仍可按全部范围生成。") }}</small>
        </div>
        <label>{{ t("当前任务") }}<input v-model="currentTask" data-current-task :placeholder="t('可选')" /></label>
        <label>{{ t("已验证状态") }}<input v-model="verifiedStatus" data-verified-status :placeholder="t('可选')" /></label>
        <label>{{ t("下一步行动") }}<input v-model="nextAction" data-next-action :placeholder="t('可选')" /></label>
        <label>{{ t("有效期") }}
          <select v-model.number="capsuleTtl"><option :value="1800">{{ t("30 分钟") }}</option><option :value="3600">{{ t("1 小时") }}</option><option :value="14400">{{ t("4 小时") }}</option><option :value="86400">{{ t("24 小时") }}</option></select>
        </label>
        <label>{{ t("Token 上限") }}
          <select v-model.number="capsuleBudget"><option :value="800">800</option><option :value="1200">1200</option><option :value="1600">1600</option></select>
        </label>
      </div>
      <button class="command-button primary" type="button" data-capsule-build :disabled="!capsuleQuery.trim() || capsuleBusy || capsuleRebuildBusy || exportBusy || importBusy" @click="buildCapsule">
        <PackageOpen :size="16" />{{ capsuleBusy ? t("正在编译...") : t("生成并预览") }}
      </button>
      <div v-if="capsuleResult" class="capsule-preview" aria-live="polite">
        <div class="capsule-summary">
          <span><ShieldCheck :size="16" />{{ t("Secret 已排除") }}</span>
          <span><Clock3 :size="16" />{{ t("有效至 {time}", { time: formatLocalDateTime(capsuleResult.capsule.expires_at) }) }}</span>
          <span>{{ t("TTL {seconds} 秒", { seconds: capsuleResult.capsule.ttl_seconds }) }}</span>
          <span>{{ capsuleResult.capsule.estimated_tokens }} / {{ capsuleResult.capsule.token_budget }} tokens</span>
          <span>{{ t("{count} 条受管引用", { count: capsuleResult.capsule.memory_references.length }) }}</span>
        </div>
        <div v-if="capsuleResult.capsule.dropped_fields.length" class="capsule-warning">{{ t("预算已排除：{fields}", { fields: capsuleResult.capsule.dropped_fields.join(", ") }) }}</div>
        <div class="capsule-actions">
          <button class="command-button" type="button" data-capsule-validate :disabled="capsuleValidationBusy || capsuleRebuildBusy || capsuleExportBusy" @click="validateCurrentCapsule">
            <ShieldCheck :size="16" />{{ capsuleValidationBusy ? t("正在验证...") : t("验证当前 Capsule") }}
          </button>
          <button class="command-button" type="button" data-capsule-rebuild :disabled="capsuleValidationBusy || capsuleRebuildBusy || capsuleExportBusy" @click="rebuildCurrentCapsule">
            <PackageOpen :size="16" />{{ capsuleRebuildBusy ? t("正在重建...") : t("按当前 Core 重建") }}
          </button>
        </div>
        <div v-if="capsuleValidation" class="capsule-validation" data-capsule-validation :data-valid="capsuleValidation.valid">
          <strong>{{ capsuleValidation.valid ? t("验证通过") : t("验证未通过") }}</strong>
          <span>valid={{ capsuleValidation.valid ? "true" : "false" }}</span>
          <span>expired={{ capsuleValidation.expired ? "true" : "false" }} · stale={{ capsuleValidation.stale ? "true" : "false" }}</span>
          <span v-if="capsuleValidation.issues.length">issues: {{ capsuleValidation.issues.map((issue) => issue.code).join(", ") }}</span>
          <span v-else>issues: none</span>
        </div>
        <div class="capsule-references">
          <h3>{{ t("实际交付的证据") }}</h3>
          <div v-if="!capsuleResult.capsule.memory_references.length" class="empty-capsule">{{ t("当前范围没有可交付记忆。") }}</div>
          <div v-for="item in capsuleResult.capsule.memory_references" :key="`${item.memory_id}:${item.revision}`" class="capsule-reference">
            <strong>{{ item.statement }}</strong>
            <span>{{ item.type }} · {{ item.namespace }} · revision {{ item.revision }}</span>
          </div>
        </div>
        <div class="delivery-heading"><h3>{{ t("交付文本") }}</h3><button class="command-button" type="button" @click="copyCapsule"><Check v-if="capsuleCopied" :size="16" /><Copy v-else :size="16" />{{ capsuleCopied ? t("已复制") : t("复制给 Agent") }}</button></div>
        <pre>{{ capsuleResult.delivery_text }}</pre>
        <div class="capsule-file-export">
          <p>{{ t("保存为可直接阅读和分享的 UTF-8 Markdown 文件。") }}</p>
          <button class="command-button" type="button" data-capsule-export :disabled="capsuleExportBusy || capsuleRebuildBusy" @click="exportCapsuleFile">
            <Download :size="16" />{{ capsuleExportBusy ? t("正在保存...") : t("选择位置并保存") }}
          </button>
        </div>
      </div>
    </section>
  </section>
</template>

<style scoped>
header { margin-bottom: 20px; }.security-band { display: flex; align-items: center; gap: 12px; padding: 16px 18px; border: 1px solid #b9d8cf; background: var(--brand-soft); color: var(--brand); }.security-band strong,.security-band span { display: block; }.security-band span { margin-top: 3px; font-size: 12px; }
.data-sections { margin-top: 24px; border-top: 1px solid var(--border); }.data-sections > section { display: grid; grid-template-columns: 42px minmax(0,1fr); gap: 14px; padding: 24px 4px; border-bottom: 1px solid var(--border); }.section-icon { display: grid; width: 36px; height: 36px; place-items: center; border: 1px solid var(--border); border-radius: var(--radius); color: var(--brand); }
h2,h3 { margin: 0; }h2 { font-size: 16px; }h3 { font-size: 13px; }p { color: var(--text-muted); }label { display: grid; max-width: 620px; gap: 6px; margin: 14px 0 10px; color: var(--text-muted); font-size: 12px; font-weight: 600; }input,select { width: 100%; min-height: 38px; padding: 0 10px; border: 1px solid var(--border-strong); border-radius: var(--radius); background: var(--surface); }.message,.error { padding: 10px 12px; border-radius: var(--radius); overflow-wrap: anywhere; }.message { border: 1px solid #b9d8cf; background: var(--brand-soft); color: var(--brand); }.error { border: 1px solid #efc9c9; background: var(--danger-soft); color: var(--danger); }
.continuity-section { padding: 24px 4px; border-bottom: 1px solid var(--border); }
.continuity-heading { display: grid; grid-template-columns: 42px minmax(0, 1fr); gap: 14px; }
.continuity-heading p { margin: 6px 0 0; }
.capsule-form { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 14px; margin: 10px 0; }
.capsule-form label { max-width: none; }
.capsule-form .wide { grid-column: 1 / -1; }
.scope-field { display: grid; align-content: start; gap: 6px; margin: 14px 0 10px; color: var(--text-muted); font-size: 12px; font-weight: 600; }
.field-label { line-height: 1.4; }
.scope-picker { position: relative; }
.scope-picker summary { display: flex; min-height: 38px; align-items: center; justify-content: space-between; gap: 8px; padding: 0 10px; border: 1px solid var(--border-strong); border-radius: var(--radius); background: var(--surface); color: var(--text); cursor: pointer; list-style: none; }
.scope-picker summary::-webkit-details-marker { display: none; }
.scope-picker summary::after { content: "▾"; color: var(--text-muted); font-size: 12px; }
.scope-picker[open] summary::after { content: "▴"; }
.scope-options { position: absolute; z-index: 4; display: grid; width: 100%; max-height: 260px; margin-top: 4px; overflow-y: auto; border: 1px solid var(--border-strong); border-radius: var(--radius); background: var(--surface); box-shadow: 0 8px 22px rgb(29 39 44 / 12%); }
.scope-option { display: grid; grid-template-columns: 18px minmax(0, 1fr); align-items: start; gap: 9px; margin: 0; padding: 10px; border-bottom: 1px solid var(--border); color: var(--text); cursor: pointer; }
.scope-option:last-of-type { border-bottom: 0; }
.scope-option:hover { background: var(--surface-subtle); }
.scope-option input { width: 16px; min-height: 16px; margin: 2px 0 0; accent-color: var(--brand); }
.scope-option span,.scope-option small { display: block; min-width: 0; }
.scope-option strong { display: block; overflow-wrap: anywhere; font-size: 12px; }
.scope-option small { margin-top: 2px; color: var(--text-muted); font-size: 11px; font-weight: 400; }
.scope-empty { margin: 0; padding: 12px; font-size: 11px; font-weight: 400; }
.scope-load-error { color: var(--warning); font-weight: 400; }
.capsule-preview { margin-top: 16px; border-top: 1px solid var(--border); }
.capsule-summary { display: flex; flex-wrap: wrap; gap: 8px 18px; padding: 14px 0; color: var(--text-muted); font-size: 12px; }
.capsule-summary span { display: inline-flex; align-items: center; gap: 5px; }
.capsule-summary span:first-child { color: var(--brand); font-weight: 600; }
.capsule-warning { padding: 9px 10px; border: 1px solid #e1c777; background: #fff8dd; color: #705200; font-size: 12px; overflow-wrap: anywhere; }
.capsule-actions { display: flex; flex-wrap: wrap; gap: 8px; padding: 4px 0 10px; }
.capsule-validation { display: grid; gap: 3px; margin: 0 0 12px; padding: 10px 12px; border: 1px solid var(--border); background: var(--surface-subtle); font-size: 12px; overflow-wrap: anywhere; }
.capsule-validation strong { color: var(--brand); }
.capsule-validation[data-valid="false"] strong { color: var(--danger); }
.capsule-references { display: grid; gap: 8px; padding: 14px 0; }
.capsule-reference { display: grid; gap: 4px; padding: 10px 0; border-bottom: 1px solid var(--border); }
.capsule-reference strong { font-size: 13px; font-weight: 600; overflow-wrap: anywhere; }
.capsule-reference span,.empty-capsule { color: var(--text-muted); font-size: 11px; }
.delivery-heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin: 4px 0 8px; }
.capsule-preview pre { max-height: 320px; margin: 0; padding: 12px; overflow: auto; border: 1px solid var(--border); background: var(--surface-subtle); color: var(--text); font: 12px/1.55 ui-monospace, SFMono-Regular, Consolas, monospace; white-space: pre-wrap; overflow-wrap: anywhere; }
.capsule-file-export { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 12px; margin-top: 14px; }
.capsule-file-export p { margin: 0; font-size: 12px; }
@media (max-width: 680px) { .capsule-form { grid-template-columns: 1fr; }.capsule-form .wide { grid-column: auto; }.delivery-heading { align-items: flex-start; flex-direction: column; } }
@media (max-width: 680px) { .capsule-file-export { grid-template-columns: 1fr; } }
</style>
