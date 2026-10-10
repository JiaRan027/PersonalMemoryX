<script setup lang="ts">
import { FolderOpen, ShieldCheck, X } from "lucide-vue-next"
import { computed, reactive, ref } from "vue"

import { requireClient, type Connector, type ConnectorDraft } from "@/api/client"
import { useI18n } from "@/i18n"

const { t } = useI18n()

const emit = defineEmits<{ close: []; created: [connector: Connector] }>()
const draft = reactive<ConnectorDraft>({
  name: "",
  profile_id: "claude_code_hook",
  source_path: "",
  target_path: null,
  namespace: "projects/current",
  read_enabled: true,
  write_enabled: false,
})
const busy = ref(false)
const error = ref("")
const isClaudeHook = computed(() => draft.profile_id === "claude_code_hook")
const isWriteOnlyProjection = computed(() =>
  ["personal_memory_projection_file", "continuity_manifest_file"].includes(draft.profile_id),
)

function profileChanged() {
  const hook = draft.profile_id === "claude_code_hook"
  const projection = ["personal_memory_projection_file", "continuity_manifest_file"].includes(draft.profile_id)
  draft.read_enabled = !projection
  draft.write_enabled = projection || (!hook && draft.profile_id !== "generic_markdown_readonly")
  draft.source_path = projection ? null : draft.source_path
  draft.target_path = hook ? null : draft.target_path
  if (hook && draft.namespace === "general") draft.namespace = "projects/current"
}

async function authorize() {
  busy.value = true
  error.value = ""
  try {
    const client = requireClient()
    await client.previewConnector({
      profile_id: draft.profile_id,
      source_path: draft.source_path || null,
      target_path: draft.target_path || null,
      read_enabled: draft.read_enabled,
      write_enabled: draft.write_enabled,
    })
    const connector = await client.createConnector(draft)
    emit("created", connector)
  } catch {
    error.value = t("无法连接这个来源，请检查项目或文件路径以及访问权限。")
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="modal-backdrop" @click.self="$emit('close')">
    <section class="wizard" role="dialog" aria-modal="true" aria-labelledby="wizard-title">
      <header>
        <div>
          <h2 id="wizard-title">{{ t("添加连接") }}</h2>
          <p>{{ t("授权后，PersonalMemory 只处理这个来源或项目。") }}</p>
        </div>
        <button class="icon-button" type="button" :aria-label="t('关闭')" :title="t('关闭')" @click="$emit('close')"><X :size="18" /></button>
      </header>

      <form @submit.prevent="authorize">
        <label>{{ t("显示名称") }}<input v-model="draft.name" required :placeholder="t('例如：工作用 Agent')" /></label>
        <label>{{ t("接入方式") }}
          <select v-model="draft.profile_id" @change="profileChanged">
            <option value="claude_code_hook">{{ t("Claude Code 项目") }}</option>
            <option value="generic_markdown_readonly">{{ t("只读取 Markdown") }}</option>
            <option value="generic_markdown_managed">{{ t("读取并更新受管区域") }}</option>
            <option value="personal_memory_projection_file">{{ t("生成独立记忆文件") }}</option>
            <option value="continuity_manifest_file">{{ t("生成连续性启动清单（L0）") }}</option>
          </select>
        </label>
        <label v-if="!isWriteOnlyProjection">{{ isClaudeHook ? t("项目根目录") : t("读取路径") }}
          <span class="path-input"><FolderOpen :size="16" /><input v-model="draft.source_path" :required="draft.read_enabled" :placeholder="isClaudeHook ? 'C:\\Projects\\my-project' : 'C:\\Agent\\MEMORY.md'" /></span>
        </label>
        <label v-if="!isClaudeHook">{{ t("写入路径") }}
          <span class="path-input"><FolderOpen :size="16" /><input v-model="draft.target_path" :disabled="!draft.write_enabled" :placeholder="t('可选，仅写入受管区域')" /></span>
        </label>
        <label>{{ t("记忆命名空间") }}
          <input v-model="draft.namespace" required placeholder="projects/my-project" />
        </label>
        <div class="permission-row">
          <label v-if="!isWriteOnlyProjection"><input v-model="draft.read_enabled" type="checkbox" /> {{ isClaudeHook ? t("观察授权项目中的 Claude 回合") : t("自动读入变化") }}</label>
          <label v-if="!isClaudeHook"><input v-model="draft.write_enabled" type="checkbox" :disabled="draft.profile_id === 'generic_markdown_readonly'" /> {{ t("自动写出记忆") }}</label>
        </div>
        <div class="privacy-note"><ShieldCheck :size="18" /><span><strong>{{ t("数据只在本机处理") }}</strong>{{ isClaudeHook ? t("只提交当前用户消息和最终助手可见回复，不采集 thinking、工具参数或代码全文。") : (isWriteOnlyProjection ? t("只更新 PersonalMemory 受管区域，不覆盖 Agent 自有记忆或文件中的其他内容。") : t("不会上传聊天记录或扫描未授权目录。")) }}</span></div>
        <p v-if="error" class="form-error" role="alert">{{ error }}</p>
        <footer>
          <button class="command-button" type="button" @click="$emit('close')">{{ t("取消") }}</button>
          <button class="command-button primary" data-action="authorize" type="submit" :disabled="busy">{{ busy ? t("正在连接...") : (isClaudeHook ? t("授权并接入 Claude") : (isWriteOnlyProjection ? t("创建受管投影") : t("授权并开始同步"))) }}</button>
        </footer>
      </form>
    </section>
  </div>
</template>

<style scoped>
.modal-backdrop { position: fixed; inset: 0; z-index: 20; display: grid; place-items: center; padding: 24px; background: rgb(23 32 38 / 36%); }
.wizard { width: min(620px, 100%); max-height: calc(100vh - 48px); overflow: auto; border: 1px solid var(--border); border-radius: 8px; background: var(--surface); box-shadow: var(--shadow); }
header { display: flex; align-items: flex-start; justify-content: space-between; padding: 20px 22px 16px; border-bottom: 1px solid var(--border); }
h2 { margin: 0; font-size: 19px; }
header p { margin: 6px 0 0; color: var(--text-muted); }
form { display: grid; gap: 16px; padding: 20px 22px 22px; }
label { display: grid; gap: 7px; font-weight: 600; }
input,
select { width: 100%; min-height: 38px; padding: 0 10px; border: 1px solid var(--border-strong); border-radius: var(--radius); background: var(--surface); color: var(--text); }
.path-input { display: grid; grid-template-columns: 24px 1fr; align-items: center; padding-left: 8px; border: 1px solid var(--border-strong); border-radius: var(--radius); color: var(--text-muted); }
.path-input input { border: 0; }
.permission-row { display: flex; gap: 24px; }
.permission-row label { display: flex; align-items: center; gap: 8px; font-weight: 400; }
.permission-row input { width: 16px; min-height: 16px; }
.privacy-note { display: flex; gap: 10px; padding: 12px; border: 1px solid #bbd9d0; border-radius: var(--radius); background: var(--brand-soft); color: var(--brand); }
.privacy-note strong { display: block; margin-bottom: 2px; }
.form-error { margin: 0; color: var(--danger); }
footer { display: flex; justify-content: flex-end; gap: 8px; padding-top: 4px; }
@media (max-width: 760px) {
  .modal-backdrop { align-items: start; padding: 10px; overflow: auto; }
  .wizard { max-height: none; }
  header { padding: 16px; }
  form { gap: 13px; padding: 16px; }
  .permission-row { display: grid; gap: 10px; }
  footer { position: sticky; bottom: 0; padding-top: 10px; background: var(--surface); }
}
</style>
