<script setup lang="ts">
import {
  Activity,
  Brain,
  Database,
  FolderInput,
  House,
  Share2,
  RefreshCw,
  CircleAlert,
  Settings,
} from "lucide-vue-next"
import { computed, onMounted } from "vue"
import { RouterLink, RouterView } from "vue-router"

import { useAppStore } from "@/stores/app"
import { useI18n } from "@/i18n"

const appStore = useAppStore()
const { t } = useI18n()

const navigation = [
  { label: "首页", to: "/", icon: House },
  { label: "记忆", to: "/memories", icon: Brain },
  { label: "图谱", to: "/graph", icon: Share2 },
  { label: "接入", to: "/connections", icon: FolderInput },
  { label: "活动", to: "/activity", icon: Activity },
  { label: "迁移", to: "/data-security", icon: Database },
  { label: "设置", to: "/settings", icon: Settings },
]

const disconnected = computed(() => appStore.serviceState === "disconnected")

onMounted(() => appStore.connect())
</script>

<template>
  <div class="app-shell">
    <aside class="sidebar">
      <div class="brand">
        <div class="brand-mark" aria-hidden="true">P</div>
        <div>
          <strong>PersonalMemory</strong>
          <span>{{ t("个人连续性中心") }}</span>
        </div>
      </div>

      <nav :aria-label="t('主要导航')">
        <RouterLink v-for="item in navigation" :key="item.to" :to="item.to">
          <component :is="item.icon" :size="18" aria-hidden="true" />
          <span>{{ t(item.label) }}</span>
        </RouterLink>
      </nav>

      <div class="local-state">
        <span class="state-dot" :class="appStore.serviceState" />
        <div>
          <strong>{{ disconnected ? t("服务未连接") : t("本机运行中") }}</strong>
          <span>{{ t("数据保存在这台电脑") }}</span>
        </div>
        <button
          v-if="disconnected"
          class="icon-button"
          type="button"
          :aria-label="t('重新连接')"
          :title="t('重新连接')"
          @click="appStore.connect"
        >
          <RefreshCw :size="16" />
        </button>
      </div>
    </aside>

    <main class="workspace">
      <div v-if="disconnected" class="service-banner" role="status">
        <CircleAlert :size="18" aria-hidden="true" />
        <span>{{ t("服务未连接，已保存的数据不会受影响。") }}</span>
        <button class="command-button" type="button" @click="appStore.connect">
          <RefreshCw :size="16" />
          {{ t("重新连接") }}
        </button>
      </div>
      <RouterView />
    </main>
  </div>
</template>

<style scoped>
.app-shell {
  display: grid;
  min-height: 100vh;
  grid-template-columns: var(--sidebar-width) minmax(0, 1fr);
}

.sidebar {
  position: fixed;
  inset: 0 auto 0 0;
  display: flex;
  width: var(--sidebar-width);
  flex-direction: column;
  padding: 20px 14px 16px;
  border-right: 1px solid var(--border);
  background: var(--surface);
}

.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 8px 20px;
}

.brand-mark {
  display: grid;
  width: 34px;
  height: 34px;
  place-items: center;
  border-radius: var(--radius);
  background: var(--brand);
  color: #ffffff;
  font-size: 17px;
  font-weight: 700;
}

.brand strong,
.brand span,
.local-state strong,
.local-state span {
  display: block;
}

.brand strong {
  font-size: 15px;
}

.brand span,
.local-state span {
  margin-top: 2px;
  color: var(--text-muted);
  font-size: 12px;
}

nav {
  display: grid;
  gap: 4px;
}

nav a {
  display: flex;
  min-height: 40px;
  align-items: center;
  gap: 10px;
  padding: 0 10px;
  border-radius: var(--radius);
  color: var(--text-muted);
  text-decoration: none;
}

nav a:hover {
  background: var(--surface-subtle);
  color: var(--text);
}

nav a.router-link-active {
  background: var(--brand-soft);
  color: var(--brand);
  font-weight: 600;
}

.local-state {
  display: grid;
  grid-template-columns: 10px minmax(0, 1fr) auto;
  align-items: center;
  gap: 9px;
  margin-top: auto;
  padding: 12px 8px 0;
  border-top: 1px solid var(--border);
}

.state-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--brand);
}

.state-dot.disconnected,
.state-dot.protected {
  background: var(--danger);
}

.state-dot.connecting,
.state-dot.degraded {
  background: var(--warning);
}

.workspace {
  display: flex;
  height: 100vh;
  min-width: 0;
  flex-direction: column;
  grid-column: 2;
  overflow: auto;
}

.service-banner {
  display: flex;
  min-height: 52px;
  align-items: center;
  gap: 10px;
  padding: 8px 24px;
  border-bottom: 1px solid #efc9c9;
  background: var(--danger-soft);
  color: var(--danger);
}

.service-banner span {
  flex: 1;
}

@media (max-width: 760px) {
  .app-shell {
    display: block;
  }

  .sidebar {
    position: static;
    width: 100%;
    padding: 12px;
    border-right: 0;
    border-bottom: 1px solid var(--border);
  }

  .brand {
    padding: 0 2px 12px;
  }

  nav {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  nav a {
    min-width: 0;
    justify-content: center;
    padding: 0 5px;
    font-size: 13px;
  }

  .local-state {
    margin-top: 10px;
    padding: 10px 2px 0;
  }

  .workspace {
    height: auto;
    grid-column: auto;
  }

  .service-banner {
    flex-wrap: wrap;
    padding: 9px 14px;
  }

  .service-banner .command-button {
    margin-left: 28px;
  }
}
</style>
