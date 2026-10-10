<script setup lang="ts">
import { CircleAlert, CircleCheck, LoaderCircle, PauseCircle } from "lucide-vue-next"
import { computed } from "vue"
import { useI18n } from "@/i18n"

const props = defineProps<{ state: string; snapshot?: boolean }>()
const { t } = useI18n()

const presentation = computed(() => {
  if (props.state === "ready" || props.state === "healthy") {
    return { label: props.snapshot ? t("来源文件可读取") : t("运行正常"), icon: CircleCheck, tone: "healthy" }
  }
  if (props.state === "syncing" || props.state === "connecting") {
    return { label: t("正在同步"), icon: LoaderCircle, tone: "working" }
  }
  if (props.state === "paused") {
    return { label: t("已暂停"), icon: PauseCircle, tone: "paused" }
  }
  if (props.state === "unsupported") {
    return { label: t("当前不支持"), icon: CircleAlert, tone: "unsupported" }
  }
  return { label: props.state === "attention" ? t("需要处理") : t("连接不可用"), icon: CircleAlert, tone: "attention" }
})
</script>

<template>
  <span class="status" :class="presentation.tone">
    <component :is="presentation.icon" :size="16" aria-hidden="true" />
    {{ presentation.label }}
  </span>
</template>

<style scoped>
.status {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--text-muted);
  font-size: 13px;
}
.healthy { color: var(--brand); }
.working,
.paused { color: var(--warning); }
.unsupported { color: var(--text-muted); }
.attention { color: var(--danger); }
</style>
