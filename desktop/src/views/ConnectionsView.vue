<script setup lang="ts">
import { Check, Code2, Copy, PlugZap, Plus, RefreshCw, ScanSearch, ShieldCheck, Unplug } from "lucide-vue-next"
import { invoke } from "@tauri-apps/api/core"
import { computed, onMounted, onUnmounted, ref } from "vue"
import { RouterLink } from "vue-router"

import { requireClient, type ActivityItem, type Connector } from "@/api/client"
import ConnectorRow from "@/components/ConnectorRow.vue"
import ConnectorWizard from "@/components/ConnectorWizard.vue"
import { isContinuousConnector } from "@/connectors/presentation"
import { useI18n } from "@/i18n"
import { formatLocalDateTime } from "@/utils/dateTime"

const { t } = useI18n()

const connectors = ref<Connector[]>([])
const activities = ref<ActivityItem[]>([])
const loading = ref(true)
const wizardOpen = ref(false)
const busyId = ref<string | null>(null)
const error = ref("")
const copied = ref("")
const notice = ref("")
interface ClaudeGlobalIntegrationStatus {
  rootPath: string
  settingsPath: string
  mcpPath: string
  settingsInstalled: boolean
  mcpInstalled: boolean
  mcpConflict: boolean
}
interface CodexIntegrationStatus {
  rootPath: string
  configPath: string
  guidancePath: string
  hooksPath: string
  mcpInstalled: boolean
  guidanceInstalled: boolean
  hooksInstalled: boolean
  installed: boolean
  conflict: boolean
}

const globalStatus = ref<ClaudeGlobalIntegrationStatus | null>(null)
const codexStatus = ref<CodexIntegrationStatus | null>(null)
const globalBusy = ref(false)
const codexBusy = ref(false)
const codexFeedback = ref("")
const codexFeedbackError = ref(false)
const codexRemovalPending = ref(false)
const codexMcpReady = ref<boolean | null>(null)
const codexChecking = ref(false)
const codexCheckedAt = ref("")
let codexPoll: ReturnType<typeof setInterval> | null = null
let viewActive = true
const globalConnector = computed(() => connectors.value.find((item) => item.profile_id === "claude_code_global_context") ?? null)
const codexConnector = computed(() => connectors.value.find((item) => item.profile_id === "codex_global_context") ?? null)
const codexCaptureReady = computed(() => Boolean(
  codexConnector.value?.enabled && codexStatus.value?.hooksInstalled,
))
const codexConfigured = computed(() => Boolean(
  codexCaptureReady.value && codexStatus.value?.mcpInstalled && codexStatus.value?.guidanceInstalled,
))
const codexVerified = computed(() => Boolean(
  codexConnector.value?.hook_receipt?.user_message && codexConnector.value?.hook_receipt?.assistant_message,
))
const claudeCaptureReady = computed(() => Boolean(
  globalConnector.value?.enabled && globalStatus.value?.settingsInstalled,
))
const claudeMemoryReadReady = computed(() => Boolean(globalStatus.value?.mcpInstalled))
const globalReady = computed(() => Boolean(
  claudeCaptureReady.value && claudeMemoryReadReady.value,
))
const claudeStarted = computed(() => Boolean(
  globalConnector.value || globalStatus.value?.settingsInstalled || globalStatus.value?.mcpInstalled || globalStatus.value?.mcpConflict,
))
const claudeStateLabel = computed(() => {
  if (globalReady.value) return t("已接入")
  if (globalStatus.value?.mcpConflict) return t("配置冲突")
  if (claudeMemoryReadReady.value && !claudeCaptureReady.value) return t("自动记录未启用")
  if (claudeCaptureReady.value && !claudeMemoryReadReady.value) return t("记忆读取未启用")
  return claudeStarted.value ? t("配置未完成") : t("未接入")
})
const claudeActionLabel = computed(() => {
  if (globalStatus.value?.mcpConflict) return t("需要手动处理冲突")
  if (!claudeStarted.value) return t("接入 Claude Code")
  if (claudeMemoryReadReady.value && !claudeCaptureReady.value) return t("启用自动记录")
  if (claudeCaptureReady.value && !claudeMemoryReadReady.value) return t("启用记忆读取")
  return t("完成接入")
})
const claudeCapabilityHint = computed(() => {
  if (globalStatus.value?.mcpConflict) return "已有同名 MCP 配置，PersonalMemory 没有覆盖它。"
  if (claudeMemoryReadReady.value && !claudeCaptureReady.value) return "记忆读取已经连接；启用自动记录后，Claude 中明确的长期信息才能进入 PersonalMemory。"
  if (claudeCaptureReady.value && !claudeMemoryReadReady.value) return "自动记录已经启用；连接记忆读取后，Claude 才能在回答时使用已有记忆。"
  if (claudeStarted.value && !globalReady.value) return "接入尚未完成；继续后只会补齐 PersonalMemory 缺少的配置。"
  return ""
})
const codexStateLabel = computed(() => {
  if (!codexStatus.value) return ""
  if (codexConfigured.value && codexMcpReady.value === false) return t("记忆读取暂不可用")
  if (codexConfigured.value && codexMcpReady.value && codexVerified.value) return t("记忆读取与自动记录入口均已验证")
  if (codexConfigured.value && codexMcpReady.value) return t("记忆读取已验证 · 尚未收到 Hook 事件")
  if (codexConfigured.value) return t("正在验证接入…")
  if (codexStatus.value.conflict) return t("配置冲突")
  const started = Boolean(codexConnector.value || codexStatus.value.mcpInstalled || codexStatus.value.guidanceInstalled || codexStatus.value.hooksInstalled)
  if (!started) return t("未接入")
  if (!codexStatus.value.hooksInstalled || !codexConnector.value?.enabled) return t("自动记录未启用")
  if (!codexStatus.value.mcpInstalled) return t("记忆读取未启用")
  if (!codexStatus.value.guidanceInstalled) return t("主动召回未启用")
  return t("配置未完成")
})
const codexActionLabel = computed(() => {
  if (!codexStatus.value) return ""
  if (codexStatus.value.conflict) return t("需要手动处理冲突")
  if (codexStatus.value.mcpInstalled && codexStatus.value.guidanceInstalled && !codexStatus.value.hooksInstalled) return t("启用自动记录")
  if (codexStatus.value.mcpInstalled && codexStatus.value.hooksInstalled && !codexStatus.value.guidanceInstalled) return t("启用主动召回")
  return t("接入 Codex")
})
const codexCapabilityHint = computed(() => {
  if (!codexStatus.value) return ""
  if (codexStatus.value.conflict) return "已有同名 MCP 配置，PersonalMemory 没有覆盖它。"
  if (!codexStatus.value.hooksInstalled && (codexStatus.value.mcpInstalled || codexStatus.value.guidanceInstalled)) return "记忆读取配置已经存在；启用自动记录后，Codex 中明确的长期信息才能进入 PersonalMemory。"
  if (!codexStatus.value.mcpInstalled && codexStatus.value.hooksInstalled) return "自动记录已经配置；连接 MCP 后，Codex 才能读取已有记忆。"
  if (!codexStatus.value.guidanceInstalled && codexStatus.value.mcpInstalled && codexStatus.value.hooksInstalled) return "读取和自动记录已配置；启用主动召回后，Codex 才会在相关问题中主动读取记忆。"
  if (codexConfigured.value && codexVerified.value) return "已收到 Codex 的消息提交和回复结束回执。它只证明 Hook 已运行，不代表普通对话会保存为长期记忆。"
  if (codexConfigured.value) return "配置文件存在，但 PersonalMemory 尚未收到 Codex 的真实 Hook 事件。Codex 的审核状态无法由本机读取；审核后需由新任务实际触发 Hook，页面才会自动验证。"
  return ""
})
const continuousConnectors = computed(() => connectors.value.filter(
  (item) => !["claude_code_global_context", "codex_global_context"].includes(item.profile_id) && isContinuousConnector(item),
))
const importedConnectors = computed(() => connectors.value.filter(
  (item) => !["claude_code_global_context", "codex_global_context"].includes(item.profile_id) && !isContinuousConnector(item),
))
const latestActivityByConnector = computed(() => {
  const latest = new Map<string, ActivityItem>()
  for (const activity of activities.value) {
    if (activity.connector_id && activity.kind === "source_ingested" && !latest.has(activity.connector_id)) {
      latest.set(activity.connector_id, activity)
    }
  }
  return latest
})
const mcpConfig = computed(() => {
  const runtime = window.__PERSONALMEMORY__
  return runtime?.mcpCommand && runtime.mcpJson
    ? { command: runtime.mcpCommand, json: runtime.mcpJson }
    : null
})

async function copyMcp(value: string, label: string) {
  try {
    await navigator.clipboard.writeText(value)
    copied.value = label
    window.setTimeout(() => { if (copied.value === label) copied.value = "" }, 1800)
  } catch {
    error.value = t("复制失败，请检查桌面端剪贴板权限。")
  }
}

async function load() {
  loading.value = true
  error.value = ""
  try {
    const client = requireClient()
    connectors.value = (await client.connectors()).items
    try { activities.value = (await client.activities()).items }
    catch { activities.value = [] }
  }
  catch { error.value = t("暂时无法读取连接状态。") }
  finally { loading.value = false }
}

async function loadGlobalStatus() {
  try {
    globalStatus.value = await invoke<ClaudeGlobalIntegrationStatus>("get_claude_global_integration")
  } catch {
    globalStatus.value = null
  }
}

async function loadCodexStatus() {
  try {
    codexStatus.value = await invoke<CodexIntegrationStatus>("get_codex_integration")
  } catch {
    codexStatus.value = null
  }
}

async function recheckCodex(automatic = false) {
  if (codexBusy.value || codexChecking.value) return
  codexChecking.value = true
  codexFeedbackError.value = false
  if (!automatic) codexFeedback.value = t("正在检查本机服务和 Codex 配置…")
  try {
    const health = await requireClient().health()
    if (health.status !== "ready") throw new Error(t("本机服务尚未就绪"))
    const [list, status] = await Promise.all([requireClient().connectors(), invoke<CodexIntegrationStatus>("get_codex_integration")])
    connectors.value = list.items
    codexStatus.value = status
    if (status.mcpInstalled && (!automatic || codexMcpReady.value !== true)) {
      await invoke("verify_personalmemory_mcp")
      codexMcpReady.value = true
    }
    codexCheckedAt.value = new Date().toISOString()
    if (!automatic) codexFeedback.value = t("检查完成：本机服务正常。{state}", {
      state: codexVerified.value ? t("已收到 Codex 的消息提交和回复完成回执。")
        : t("还没收到完整的 Hook 回执。请在新 Codex 任务中发送普通消息并等待回复完成；本页会自动刷新，无需再次检查。"),
    })
  } catch (failure) {
    codexMcpReady.value = false
    codexFeedbackError.value = true
    codexFeedback.value = t("检查失败：{reason}", { reason: failure instanceof Error ? failure.message : String(failure) })
  }
  finally { codexChecking.value = false }
}

async function installGlobalClaude() {
  if (!globalStatus.value) return
  globalBusy.value = true
  error.value = ""
  notice.value = ""
  try {
    const client = requireClient()
    let connector = globalConnector.value
    const hadConnector = Boolean(connector)
    if (!connector) {
      connector = await client.createConnector({
        name: "Claude Code 个人连续性",
        profile_id: "claude_code_global_context",
        source_path: globalStatus.value.rootPath,
        target_path: null,
        namespace: "general",
        read_enabled: true,
        write_enabled: false,
      })
    } else if (!connector.enabled || !connector.read_enabled) {
      connector = await client.patchConnector(connector.id, { enabled: true, read_enabled: true })
    }
    if (globalStatus.value.mcpInstalled && (!globalStatus.value.settingsInstalled || !hadConnector)) {
      await invoke("enable_claude_global_capture", { connectorId: connector.id })
      notice.value = "Claude Code 自动记录已启用。重新打开 Claude 会话后生效。"
    } else if (globalStatus.value.settingsInstalled && !globalStatus.value.mcpInstalled && hadConnector) {
      await invoke("enable_claude_global_memory_read")
      notice.value = "Claude Code 记忆读取已启用。重新打开 Claude 会话后生效。"
    } else if (!globalStatus.value.settingsInstalled || !globalStatus.value.mcpInstalled) {
      await invoke("install_claude_global_integration", { connectorId: connector.id })
      notice.value = "Claude Code 个人连续性已接入。重新打开 Claude 会话后生效。"
    } else {
      notice.value = "Claude Code 的记录来源已启用。"
    }
    await Promise.all([load(), loadGlobalStatus()])
  } catch {
    error.value = "全局接入未完成；现有 Claude 配置未被强制覆盖，可以重试或检查配置文件格式。"
  } finally {
    globalBusy.value = false
  }
}

async function removeGlobalClaude() {
  if (!window.confirm(t("移除 Claude Code 全局个人连续性？Memory Core 和项目级接入都会保留。"))) return
  globalBusy.value = true
  error.value = ""
  notice.value = ""
  try {
    await invoke("remove_claude_global_integration")
    if (globalConnector.value) await requireClient().removeConnector(globalConnector.value.id)
    await Promise.all([load(), loadGlobalStatus()])
    notice.value = "Claude Code 全局接入已移除；Memory Core 和项目级接入保持不变。"
  } catch {
    error.value = "无法完整移除全局接入；为避免破坏 Claude 配置，没有继续执行不确定的删除。"
  } finally {
    globalBusy.value = false
  }
}

async function installCodex() {
  if (!codexStatus.value) return
  codexBusy.value = true
  codexFeedbackError.value = false
  codexFeedback.value = t("正在修复 Codex 配置…")
  error.value = ""
  notice.value = ""
  try {
    const client = requireClient()
    let connector = codexConnector.value
    if (!connector) {
      connector = await client.createConnector({
        name: "Codex 个人连续性",
        profile_id: "codex_global_context",
        source_path: codexStatus.value.rootPath,
        target_path: null,
        namespace: "general",
        read_enabled: true,
        write_enabled: false,
      })
    } else if (!connector.enabled || !connector.read_enabled) {
      connector = await client.patchConnector(connector.id, { enabled: true, read_enabled: true })
    }
    if (codexStatus.value.mcpInstalled && codexStatus.value.guidanceInstalled && !codexStatus.value.hooksInstalled) {
      codexStatus.value = await invoke<CodexIntegrationStatus>("enable_codex_capture", { connectorId: connector.id })
      notice.value = "Codex 自动记录已配置。请到 Codex 设置 → Hooks 审核两条 PersonalMemory 用户 Hook，然后新建任务。"
    } else if (codexStatus.value.mcpInstalled && codexStatus.value.hooksInstalled && !codexStatus.value.guidanceInstalled) {
      codexStatus.value = await invoke<CodexIntegrationStatus>("enable_codex_active_recall")
      notice.value = "Codex 主动召回已启用。请新建 Codex 任务后使用；当前已打开的任务不会动态加载。"
    } else {
      codexStatus.value = await invoke<CodexIntegrationStatus>("install_codex_integration", { connectorId: connector.id })
      notice.value = "Codex 读取、主动召回与自动记录已配置。请到 Codex 设置 → Hooks 审核两条 PersonalMemory 用户 Hook，然后新建任务。"
    }
    await load()
    codexFeedback.value = t(notice.value)
  } catch (failure) {
    codexFeedbackError.value = true
    codexFeedback.value = t("修复失败：{reason}", { reason: failure instanceof Error ? failure.message : String(failure) })
    error.value = codexStatus.value?.conflict
      ? "Codex 已存在同名但不属于 PersonalMemory 的 MCP；为保护现有配置，没有覆盖。"
      : "Codex 接入未完成；现有 config.toml 已保留，可以检查配置格式后重试。"
  } finally {
    codexBusy.value = false
    await loadCodexStatus()
    codexMcpReady.value = null
    if (codexConfigured.value) await recheckCodex(true)
  }
}

async function removeCodex() {
  if (!codexRemovalPending.value) return
  codexRemovalPending.value = false
  codexBusy.value = true
  error.value = ""
  notice.value = ""
  try {
    codexStatus.value = await invoke<CodexIntegrationStatus>("remove_codex_integration")
    if (codexConnector.value) await requireClient().removeConnector(codexConnector.value.id)
    await load()
    notice.value = "Codex MCP、Hook 与受管连续性规则已移除；Memory Core 和其他 Agent 配置保持不变。"
  } catch {
    error.value = "无法安全移除 Codex 接入；未识别的配置不会被删除。"
  } finally {
    codexBusy.value = false
    await loadCodexStatus()
  }
}

async function sync(connector: Connector, initial = false) {
  busyId.value = connector.id
  try { await requireClient().syncConnector(connector.id); await load() }
  catch {
    if (initial) notice.value = "连接已创建，但首次同步失败，可以稍后重试。"
    else error.value = "同步没有完成，请稍后重试。"
  }
  finally { busyId.value = null }
}

async function toggle(connector: Connector) {
  busyId.value = connector.id
  error.value = ""
  notice.value = ""
  try {
    await requireClient().patchConnector(connector.id, { enabled: !connector.enabled })
    await load()
  } catch {
    error.value = "更新连接状态未完成，请稍后重试。"
  } finally {
    busyId.value = null
  }
}

async function remove(connector: Connector) {
  if (!window.confirm(t("移除连接「{name}」？已导入的记忆会保留，但该来源将不再自动同步。", { name: connector.name }))) return
  busyId.value = connector.id
  error.value = ""
  notice.value = ""
  try {
    if (connector.profile_id === "claude_code_global_context") {
      await invoke("remove_claude_global_integration")
    } else if (connector.profile_id === "claude_code_hook" && connector.source_path) {
      await invoke("remove_claude_project_integration", { projectRoot: connector.source_path })
    }
    await requireClient().removeConnector(connector.id)
    await load()
  } catch {
    error.value = "移除连接未完成；Claude 项目配置和 Memory Core 不会被部分删除。"
  } finally {
    busyId.value = null
  }
}

async function installClaude(connector: Connector) {
  if (!connector.source_path) return
  busyId.value = connector.id
  error.value = ""
  notice.value = ""
  try {
    await invoke("install_claude_project_integration", {
      projectRoot: connector.source_path,
      connectorId: connector.id,
    })
    notice.value = "Claude Code 项目接入已安装。重新打开该项目的 Claude 会话后生效。"
  } catch {
    error.value = "Claude Code 接入未完成；原配置未被覆盖，请检查项目路径或 JSON 格式。"
  } finally {
    busyId.value = null
  }
}

async function disconnectClaude(connector: Connector) {
  if (!connector.source_path || !window.confirm(t("只移除这个项目中的 PersonalMemory Hook 和 MCP 配置？Memory Core 数据会保留。"))) return
  busyId.value = connector.id
  error.value = ""
  notice.value = ""
  try {
    await invoke("remove_claude_project_integration", { projectRoot: connector.source_path })
    notice.value = "项目级 Claude Code 接入已移除，Memory Core 数据保持不变。"
  } catch {
    error.value = "无法安全移除接入；未识别的项目配置不会被覆盖。"
  } finally {
    busyId.value = null
  }
}

async function connectorCreated(connector: Connector) {
  wizardOpen.value = false
  await load()
  if (connector.profile_id === "claude_code_hook") await installClaude(connector)
  else await sync(connector, true)
}

onMounted(async () => {
  await Promise.all([load(), loadGlobalStatus(), loadCodexStatus()])
  if (codexConfigured.value) await recheckCodex(true)
  if (!viewActive) return
  codexPoll = setInterval(() => {
    if (!document.hidden && codexConfigured.value) void recheckCodex(true)
  }, 10_000)
})
onUnmounted(() => { viewActive = false; if (codexPoll) clearInterval(codexPoll) })
</script>

<template>
  <section class="page">
    <header class="page-header">
      <div><h1 class="page-title">{{ t("来源与接入") }}</h1><p class="page-subtitle">{{ t("持续接入 Agent，或从本机已有记录中导入记忆。") }}</p></div>
      <div class="header-actions">
        <RouterLink class="command-button" to="/recognition"><ScanSearch :size="17" />{{ t("扫描本机来源") }}</RouterLink>
        <button class="command-button primary" type="button" :aria-label="t('添加连接')" @click="wizardOpen = true"><Plus :size="17" />{{ t("添加持续来源") }}</button>
      </div>
    </header>
    <p v-if="error" class="inline-error" role="alert">{{ t(error) }}</p>
    <p v-if="notice" class="inline-notice" role="status">{{ t(notice) }}</p>
    <section v-if="globalStatus" class="global-continuity" aria-labelledby="global-continuity-title">
      <div class="global-icon" aria-hidden="true"><ShieldCheck :size="22" /></div>
      <div class="global-copy">
        <div class="global-title-row">
          <h2 id="global-continuity-title">{{ t("让 Claude 在你的个人目录中认识你") }}</h2>
          <span class="global-state" :class="{ ready: globalReady }">{{ claudeStateLabel }}</span>
        </div>
        <p>{{ t("回答前自动交付已确认的个人身份、目标和偏好；只沉淀明确的个人身份、长期目标、决定和偏好，不保存普通提问或完整聊天。项目经验仍由项目级连接隔离。") }}</p>
        <p v-if="claudeCapabilityHint" class="capability-hint">{{ t(claudeCapabilityHint) }}</p>
        <details class="advanced-config">
          <summary>{{ t("查看授权范围") }}</summary>
          <code class="mcp-command">{{ globalStatus.rootPath }}</code>
        </details>
      </div>
      <button v-if="globalReady" class="command-button" type="button" :disabled="globalBusy" @click="removeGlobalClaude">
        <Unplug :size="16" />{{ t("移除全局接入") }}
      </button>
      <button v-else class="command-button primary" type="button" :disabled="globalBusy || globalStatus.mcpConflict" @click="installGlobalClaude">
        <RefreshCw v-if="globalBusy" :size="16" class="spin" /><PlugZap v-else :size="16" />
        {{ claudeActionLabel }}
      </button>
    </section>
    <section v-if="codexStatus" class="global-continuity codex-continuity" aria-labelledby="codex-continuity-title">
      <div class="global-icon codex-icon" aria-hidden="true"><Code2 :size="22" /></div>
      <div class="global-copy">
        <div class="global-title-row">
          <h2 id="codex-continuity-title">{{ t("让 Codex 使用 PersonalMemory") }}</h2>
          <span class="global-state" :class="{ ready: codexVerified }">{{ codexStateLabel }}</span>
        </div>
        <p>{{ t("接入后，新 Codex 任务会主动读取相关记忆，并选择性沉淀明确的个人身份、长期目标、决定和偏好；普通提问和完整聊天不会保存。Codex Desktop 需要在“设置 → Hooks”审核两条用户 Hook。") }}</p>
        <p v-if="codexCapabilityHint" class="capability-hint">{{ t(codexCapabilityHint) }}</p>
        <ul v-if="codexConfigured" class="codex-checks">
          <li>{{ t("记忆读取") }}：{{ codexMcpReady === true ? t("已验证可用") : codexMcpReady === false ? t("暂不可用") : t("检查中") }}</li>
          <li>{{ t("用户发送消息") }}：{{ codexConnector?.hook_receipt?.user_message ? formatLocalDateTime(codexConnector.hook_receipt.user_message) : t("尚未收到 Codex Hook 事件") }}</li>
          <li>{{ t("Codex 回复完成") }}：{{ codexConnector?.hook_receipt?.assistant_message ? formatLocalDateTime(codexConnector.hook_receipt.assistant_message) : t("尚未收到 Codex Hook 事件") }}</li>
        </ul>
        <p v-if="codexConfigured && !codexVerified" class="codex-verification">
          <strong>{{ t("如何确认 Codex 接入正常") }}</strong>
          <span>1. {{ t("配置存在不代表 Codex 已批准；PersonalMemory 读不到 Codex 的审核状态。若已审核，可直接继续。") }}</span>
          <span>2. {{ t("审核之后新建一个 Codex 任务，发送普通消息并等回复完成；旧任务可能没有加载这两条 Hook。") }}</span>
          <span>3. {{ t("页面每 10 秒自动查真实回执。若新任务已完成一轮仍无时间记录，说明 Hook 尚未到达 PersonalMemory，而不是没有记忆可保存。") }}</span>
          <small>{{ t("回执只说明 Codex 调用了 Hook，不等于这轮对话被保存。PersonalMemory 仍只选择符合长期记忆规则的信息。") }}</small>
        </p>
        <p v-else-if="codexVerified" class="codex-verification" role="status">
          <strong>{{ t("读取与 Hook 事件均已收到") }}</strong>
          <small>{{ t("回执只说明 Codex 调用了 Hook，不等于这轮对话被保存。PersonalMemory 仍只选择符合长期记忆规则的信息。") }}</small>
        </p>
        <p v-if="codexCheckedAt" class="codex-check-time">{{ t("自动检查：{time}", { time: formatLocalDateTime(codexCheckedAt) }) }}</p>
        <details class="advanced-config">
          <summary>{{ t("查看受管配置") }}</summary>
          <code class="mcp-command">{{ codexStatus.configPath }}</code>
          <code class="mcp-command">{{ codexStatus.guidancePath }}</code>
          <code class="mcp-command">{{ codexStatus.hooksPath }}</code>
          <p>{{ t("检查验证新的本机 MCP 连接。如果当前 Codex 任务仍报 Transport closed，请重启 Codex 后继续该任务，重新建立旧会话的连接。") }}</p>
          <div class="mcp-actions">
            <button class="command-button" type="button" :disabled="codexBusy || codexChecking" @click="recheckCodex(false)">{{ t("重新检查") }}</button>
            <button v-if="codexConfigured" class="command-button" type="button" :disabled="codexBusy || codexChecking" @click="installCodex">{{ t("修复 Codex 接入") }}</button>
          </div>
        </details>
      </div>
      <div class="codex-actions">
      <button v-if="codexConfigured" class="command-button" type="button" :disabled="codexBusy" @click="codexRemovalPending = true">
        <Unplug :size="16" />{{ t("移除 Codex 接入") }}
      </button>
      <button v-else class="command-button primary" type="button" :disabled="codexBusy || codexStatus.conflict" @click="installCodex">
        <RefreshCw v-if="codexBusy" :size="16" class="spin" /><PlugZap v-else :size="16" />
        {{ codexActionLabel }}
      </button>
      </div>
      <p v-if="codexFeedback" class="codex-feedback" :class="{ failed: codexFeedbackError }" role="status">{{ codexFeedback }}</p>
      <div v-if="codexRemovalPending" class="codex-confirm">
        <p>{{ t("移除 Codex 中的 PersonalMemory MCP、Hook 与受管连续性规则？Memory Core 和其他 Agent 接入都会保留。") }}</p>
        <button class="command-button" type="button" @click="removeCodex">{{ t("确认移除") }}</button>
        <button class="command-button" type="button" @click="codexRemovalPending = false">{{ t("取消") }}</button>
      </div>
    </section>
    <section v-if="mcpConfig" class="mcp-panel" aria-labelledby="mcp-title">
      <div>
        <h2 id="mcp-title">{{ t("连接其他支持 MCP 的 Agent") }}</h2>
        <p>{{ t("支持 MCP 的 Agent 使用同一个本机入口；不复制全部记忆，只在需要时读取相关上下文。") }}</p>
      </div>
      <div class="mcp-actions">
        <button class="command-button" type="button" @click="copyMcp(mcpConfig.command, 'command')">
          <Check v-if="copied === 'command'" :size="16" /><Copy v-else :size="16" />
          {{ copied === "command" ? t("命令已复制") : t("复制命令") }}
        </button>
        <button class="command-button" type="button" @click="copyMcp(mcpConfig.json, 'json')">
          <Check v-if="copied === 'json'" :size="16" /><Copy v-else :size="16" />
          {{ copied === "json" ? t("JSON 已复制") : t("复制 JSON") }}
        </button>
      </div>
      <details class="advanced-config"><summary>{{ t("查看本机接入路径") }}</summary><code class="mcp-command">{{ mcpConfig.command }}</code></details>
    </section>
    <section class="source-section">
      <div class="section-heading"><div><h2>{{ t("持续接入") }}</h2><p>{{ t("启用的读取来源会被后台检查；暂停只停止后续同步，不会删除 Memory Core 中已有内容。") }}</p></div><span>{{ t("{count} 项", { count: continuousConnectors.length }) }}</span></div>
      <div class="panel connection-list">
        <div class="list-heading"><span>{{ t("来源与用途") }}</span><span>{{ t("状态") }}</span><span>{{ t("数据方向") }}</span><span>{{ t("操作") }}</span></div>
        <div v-if="loading" class="empty-state"><RefreshCw :size="18" class="spin" />{{ t("正在读取来源...") }}</div>
        <ConnectorRow v-for="connector in continuousConnectors" :key="connector.id" :connector="connector" :latest-activity="latestActivityByConnector.get(connector.id)" :busy="busyId === connector.id" @sync="sync(connector)" @toggle="toggle(connector)" @remove="remove(connector)" @integrate="installClaude(connector)" @disconnect-agent="disconnectClaude(connector)" />
        <div v-if="!loading && !continuousConnectors.length" class="empty-state">{{ t("还没有持续接入。添加一个项目或使用上方 MCP 配置即可开始。") }}</div>
      </div>
    </section>
    <details v-if="importedConnectors.length" class="imported-section">
      <summary><span><strong>{{ t("已导入来源") }}</strong><small>{{ t("一次性或只读导入记录，不等同于 Agent 连接") }}</small></span><span>{{ t("{count} 项", { count: importedConnectors.length }) }}</span></summary>
      <div class="panel connection-list">
        <div class="list-heading"><span>{{ t("来源与用途") }}</span><span>{{ t("状态") }}</span><span>{{ t("数据方向") }}</span><span>{{ t("操作") }}</span></div>
        <ConnectorRow v-for="connector in importedConnectors" :key="connector.id" :connector="connector" :latest-activity="latestActivityByConnector.get(connector.id)" :busy="busyId === connector.id" @sync="sync(connector)" @toggle="toggle(connector)" @remove="remove(connector)" />
      </div>
    </details>
    <ConnectorWizard v-if="wizardOpen" @close="wizardOpen = false" @created="connectorCreated" />
  </section>
</template>

<style scoped>
.page-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 20px; }
.header-actions { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 8px; }
.header-actions a { text-decoration: none; }
.global-continuity { display: grid; grid-template-columns: 40px minmax(0, 1fr) auto; align-items: center; gap: 14px; margin-bottom: 14px; padding: 16px; border: 1px solid var(--border-strong); background: var(--surface); }
.codex-continuity { grid-template-columns: 40px minmax(0, 1fr); align-items: start; }
.global-copy { min-width: 0; }
.codex-actions { grid-column: 2; display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.codex-actions .command-button { flex: 0 0 auto; white-space: nowrap; }
.codex-checks { display: flex; flex-wrap: wrap; gap: 6px 18px; margin: 10px 0; padding: 0; list-style: none; color: var(--text-muted); font-size: 12px; }
.codex-verification { display: grid; gap: 6px; margin: 10px 0; padding: 10px 12px; border-left: 3px solid var(--brand); background: var(--surface-subtle); color: var(--text); font-size: 12px; line-height: 1.55; }
.codex-verification strong { font-size: 13px; }
.codex-verification span,.codex-verification small { display: block; }
.codex-verification small { color: var(--text-muted); }
.global-copy .codex-check-time { margin: 4px 0 8px; font-size: 11px; }
.codex-feedback, .codex-confirm { grid-column: 2; margin: 0; padding: 10px 12px; border: 1px solid var(--border); background: var(--surface-subtle); font-size: 13px; line-height: 1.6; overflow-wrap: anywhere; }
.codex-feedback.failed { color: var(--danger); }
.codex-confirm p { margin: 0 0 8px; }
.codex-confirm .command-button { margin-right: 8px; white-space: nowrap; }
.global-icon { display: grid; width: 40px; height: 40px; place-items: center; border: 1px solid #b9d8cf; border-radius: var(--radius); background: var(--brand-soft); color: var(--brand); }
.global-icon.codex-icon { border-color: var(--border-strong); background: var(--surface-subtle); color: var(--text); }
.global-title-row { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 10px; }
.global-title-row h2 { margin: 0; font-size: 15px; }
.global-copy p { margin: 5px 0 0; color: var(--text-muted); font-size: 13px; line-height: 1.6; }
.global-copy .capability-hint { color: var(--text); font-weight: 600; }
.global-state { padding: 2px 7px; border: 1px solid var(--border); border-radius: 999px; color: var(--text-muted); font-size: 11px; font-weight: 600; }
.global-state.ready { border-color: #9ac9bc; background: var(--brand-soft); color: var(--brand); }
.mcp-panel { display: grid; gap: 12px; margin-bottom: 20px; padding: 16px; border: 1px solid #b9d8cf; background: var(--brand-soft); }
.mcp-panel h2 { margin: 0; font-size: 15px; }
.mcp-panel p { margin: 4px 0 0; color: var(--text-muted); font-size: 13px; }
.mcp-actions { display: flex; flex-wrap: wrap; gap: 8px; }
.advanced-config summary { cursor: pointer; color: var(--text-muted); font-size: 12px; }
.mcp-command { display: block; overflow-x: auto; margin-top: 8px; padding: 9px 10px; background: rgba(255, 255, 255, 0.72); color: var(--text); font-size: 12px; }
.source-section { margin-top: 22px; }
.section-heading { display: flex; align-items: flex-end; justify-content: space-between; gap: 16px; margin-bottom: 10px; }
.section-heading h2 { margin: 0; font-size: 16px; }
.section-heading p { margin: 4px 0 0; color: var(--text-muted); font-size: 13px; }
.section-heading > span { color: var(--text-muted); font-size: 12px; }
.imported-section { margin-top: 22px; }
.imported-section > summary { display: flex; min-height: 56px; align-items: center; justify-content: space-between; gap: 16px; padding: 0 14px; border: 1px solid var(--border); background: var(--surface); cursor: pointer; }
.imported-section summary span:first-child { display: grid; gap: 3px; }
.imported-section summary small,.imported-section summary > span:last-child { color: var(--text-muted); font-size: 12px; }
.imported-section .connection-list { border-top: 0; }
.connection-list { overflow: hidden; }
.list-heading { display: grid; grid-template-columns: minmax(180px, 1fr) 130px 240px auto; gap: 20px; padding: 10px 16px; border-bottom: 1px solid var(--border); background: var(--surface-subtle); color: var(--text-muted); font-size: 12px; font-weight: 600; }
.empty-state { display: flex; min-height: 100px; align-items: center; justify-content: center; gap: 8px; color: var(--text-muted); }
.inline-error { padding: 10px 12px; border: 1px solid #efc9c9; border-radius: var(--radius); background: var(--danger-soft); color: var(--danger); }
.inline-notice { padding: 10px 12px; border: 1px solid #b9d8cf; border-radius: var(--radius); background: var(--brand-soft); color: var(--brand); }
.spin { animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
@media (max-width: 760px) {
  .page-header { gap: 12px; }
  .page-header { flex-direction: column; }
  .header-actions { width: 100%; justify-content: flex-start; }
  .page-header .command-button { flex: 0 0 auto; padding: 0 9px; }
  .global-continuity { grid-template-columns: 40px minmax(0, 1fr); }
  .global-continuity > .command-button { grid-column: 2; justify-self: start; }
  .list-heading { display: none; }
}
</style>
