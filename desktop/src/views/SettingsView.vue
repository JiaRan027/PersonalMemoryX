<script setup lang="ts">
import { invoke } from "@tauri-apps/api/core"
import { disable, enable, isEnabled } from "@tauri-apps/plugin-autostart"
import { CircleAlert, FileText, Languages, LoaderCircle, Power, Settings2 } from "lucide-vue-next"
import { onMounted, ref } from "vue"

import { type AppLocale, type DesktopPreferences, useI18n } from "@/i18n"

const { locale, setLocale, t } = useI18n()

const autostartEnabled = ref(false)
const showWindowOnAutostart = ref(false)
const loading = ref(true)
const saving = ref(false)
const message = ref("")
const error = ref("")
const licenseText = ref("")
const licenseOpen = ref(false)
const licenseLoading = ref(false)
const thirdPartyNoticesText = ref("")
const thirdPartyNoticesOpen = ref(false)
const thirdPartyNoticesLoading = ref(false)

async function loadSettings() {
  loading.value = true
  error.value = ""
  try {
    const [enabled, preferences] = await Promise.all([
      isEnabled(),
      invoke<DesktopPreferences>("get_desktop_preferences"),
    ])
    autostartEnabled.value = enabled
    showWindowOnAutostart.value = preferences.show_window_on_autostart
    setLocale(preferences.locale === "en-US" ? "en-US" : "zh-CN")
  } catch {
    error.value = t("无法读取这台电脑的启动设置，请重新打开 PersonalMemory 后再试。")
  } finally {
    loading.value = false
  }
}

async function updateAutostart(nextValue: boolean) {
  const previousValue = autostartEnabled.value
  autostartEnabled.value = nextValue
  saving.value = true
  message.value = ""
  error.value = ""
  try {
    if (nextValue) await enable()
    else await disable()
    const actualValue = await isEnabled()
    if (actualValue !== nextValue) throw new Error("autostart state did not change")
    autostartEnabled.value = actualValue
    message.value = nextValue
      ? t("已开启。下次登录 Windows 时，PersonalMemory 会自动在后台运行。")
      : t("已关闭。PersonalMemory 不会在登录 Windows 时自动启动。")
  } catch {
    autostartEnabled.value = previousValue
    error.value = t("开机自动启动设置没有生效，已恢复原来的显示状态。")
  } finally {
    saving.value = false
  }
}

async function updateStartupWindow(nextValue: boolean) {
  const previousValue = showWindowOnAutostart.value
  showWindowOnAutostart.value = nextValue
  saving.value = true
  message.value = ""
  error.value = ""
  try {
    const preferences = await invoke<DesktopPreferences>("set_autostart_show_window", {
      showWindow: nextValue,
    })
    showWindowOnAutostart.value = preferences.show_window_on_autostart
    message.value = nextValue
      ? t("开机启动后会显示主窗口。")
      : t("开机启动后会安静地留在系统托盘。")
  } catch {
    showWindowOnAutostart.value = previousValue
    error.value = t("启动显示方式没有保存，已恢复原来的选择。")
  } finally {
    saving.value = false
  }
}

async function updateLocale(nextLocale: AppLocale) {
  const previousLocale = locale.value
  setLocale(nextLocale)
  message.value = ""
  error.value = ""
  try {
    const preferences = await invoke<DesktopPreferences>("set_desktop_locale", { locale: nextLocale })
    setLocale(preferences.locale)
  } catch {
    setLocale(previousLocale)
    error.value = t("语言设置没有保存，已恢复原来的选择。")
  }
}

async function toggleLicense() {
  if (licenseOpen.value) {
    licenseOpen.value = false
    return
  }

  licenseLoading.value = true
  error.value = ""
  try {
    licenseText.value = await invoke<string>("get_license_text")
    licenseOpen.value = true
  } catch {
    error.value = t("无法读取随应用提供的许可证文本。")
  } finally {
    licenseLoading.value = false
  }
}

async function toggleThirdPartyNotices() {
  if (thirdPartyNoticesOpen.value) {
    thirdPartyNoticesOpen.value = false
    return
  }

  thirdPartyNoticesLoading.value = true
  error.value = ""
  try {
    thirdPartyNoticesText.value = await invoke<string>("get_third_party_notices_text")
    thirdPartyNoticesOpen.value = true
  } catch {
    error.value = t("无法读取随应用提供的第三方许可清单。")
  } finally {
    thirdPartyNoticesLoading.value = false
  }
}

onMounted(loadSettings)
</script>

<template>
  <section class="page settings-page">
    <header class="page-header">
      <div>
        <h1 class="page-title">{{ t("设置") }}</h1>
        <p class="page-subtitle">{{ t("调整 PersonalMemory 在这台电脑上的语言、启动与后台行为。") }}</p>
      </div>
    </header>

    <div v-if="error" class="feedback error" role="alert">
      <CircleAlert :size="18" aria-hidden="true" />
      <span>{{ error }}</span>
      <button class="command-button" type="button" :disabled="loading || saving" @click="loadSettings">{{ t("重新读取") }}</button>
    </div>
    <div v-if="message" class="feedback success" role="status">{{ message }}</div>

    <article class="panel settings-panel">
      <div class="section-heading">
        <span class="section-icon"><Languages :size="19" aria-hidden="true" /></span>
        <div>
          <h2>{{ t("语言与显示") }}</h2>
          <p>{{ t("选择界面语言。记忆原文、证据和技术标识符不会被改写。") }}</p>
        </div>
      </div>

      <div class="setting-row language-row">
        <div class="setting-copy"><strong>{{ t("界面语言") }}</strong></div>
        <select
          data-locale-select
          class="language-select"
          :value="locale"
          :disabled="loading"
          @change="updateLocale(($event.target as HTMLSelectElement).value as AppLocale)"
        >
          <option value="zh-CN">{{ t("简体中文") }}</option>
          <option value="en-US">English</option>
        </select>
      </div>
    </article>

    <article class="panel settings-panel">
      <div class="section-heading">
        <span class="section-icon"><Power :size="19" aria-hidden="true" /></span>
        <div>
          <h2>{{ t("启动与后台") }}</h2>
          <p>{{ t("这些设置只影响当前 Windows 用户，不会改变你的记忆内容。") }}</p>
        </div>
      </div>

      <div class="setting-row">
        <div class="setting-copy">
          <strong>{{ t("开机自动启动") }}</strong>
          <span>{{ t("登录 Windows 后自动启动 PersonalMemory，让 Agent 可以随时读取连续性上下文。") }}</span>
        </div>
        <label class="switch-control">
          <input
            data-autostart-toggle
            type="checkbox"
            :checked="autostartEnabled"
            :disabled="loading || saving"
            @change="updateAutostart(($event.target as HTMLInputElement).checked)"
          />
          <span class="switch-track" aria-hidden="true"><span /></span>
          <span class="switch-label">{{ loading ? t("读取中") : autostartEnabled ? t("已开启") : t("已关闭") }}</span>
        </label>
      </div>

      <div class="setting-row secondary" :class="{ disabled: !autostartEnabled }">
        <div class="setting-copy">
          <strong>{{ t("开机后显示主窗口") }}</strong>
          <span>{{ t("关闭时会静默启动并停留在系统托盘，不会弹出窗口或常驻终端。") }}</span>
        </div>
        <label class="switch-control">
          <input
            data-startup-window-toggle
            type="checkbox"
            :checked="showWindowOnAutostart"
            :disabled="loading || saving || !autostartEnabled"
            @change="updateStartupWindow(($event.target as HTMLInputElement).checked)"
          />
          <span class="switch-track" aria-hidden="true"><span /></span>
          <span class="switch-label">{{ loading ? t("读取中") : showWindowOnAutostart ? t("显示窗口") : t("后台运行") }}</span>
        </label>
      </div>

      <div v-if="loading" class="loading-state" role="status">
        <LoaderCircle :size="17" class="spin" aria-hidden="true" />
        {{ t("正在读取系统设置…") }}
      </div>
    </article>

    <article class="panel settings-panel information-panel">
      <div class="section-heading">
        <span class="section-icon"><Settings2 :size="19" aria-hidden="true" /></span>
        <div>
          <h2>{{ t("关于后台运行") }}</h2>
          <p>{{ t("关闭主窗口时，PersonalMemory 会继续在系统托盘运行。需要完全退出时，请使用托盘菜单中的“退出”。") }}</p>
        </div>
      </div>
    </article>

    <article class="panel settings-panel legal-panel" aria-labelledby="legal-notice-title">
      <div class="section-heading">
        <span class="section-icon"><FileText :size="19" aria-hidden="true" /></span>
        <div>
          <h2 id="legal-notice-title">{{ t("法律声明") }}</h2>
          <p>{{ t("版权 © 2026 JiaRan027") }}</p>
        </div>
      </div>
      <div class="legal-copy">
        <p><strong>{{ t("许可证") }}：</strong>GNU Affero General Public License v3.0 or later (AGPL-3.0-or-later).</p>
        <p>{{ t("本程序按“原样”提供，不附带任何明示或默示担保。你可以依照 AGPL-3.0-or-later 使用、修改和再分发本程序。") }}</p>
        <p><strong>{{ t("项目源码") }}：</strong><code>https://github.com/JiaRan027/PersonalMemory</code></p>
        <button
          class="command-button"
          type="button"
          :disabled="licenseLoading"
          data-license-toggle
          @click="toggleLicense"
        >{{ licenseLoading ? t("读取中") : licenseOpen ? t("隐藏许可证全文") : t("查看许可证全文") }}</button>
        <pre v-if="licenseOpen" class="license-text" data-license-text tabindex="0">{{ licenseText }}</pre>
        <p>{{ t("第三方依赖及其许可文本随安装包提供。") }}</p>
        <button
          class="command-button"
          type="button"
          :disabled="thirdPartyNoticesLoading"
          data-third-party-toggle
          @click="toggleThirdPartyNotices"
        >{{ thirdPartyNoticesLoading ? t("读取中") : thirdPartyNoticesOpen ? t("隐藏第三方许可清单") : t("查看第三方许可清单") }}</button>
        <pre v-if="thirdPartyNoticesOpen" class="license-text" data-third-party-notices tabindex="0">{{ thirdPartyNoticesText }}</pre>
      </div>
    </article>
  </section>
</template>

<style scoped>
.settings-page { width: 100%; max-width: 960px; }
.page-header { margin-bottom: 22px; }
.settings-panel { padding: 0 22px; }
.settings-panel + .settings-panel { margin-top: 16px; }
.section-heading { display: flex; gap: 12px; padding: 22px 0 18px; }
.section-heading h2 { margin: 0; font-size: 16px; }
.section-heading p { margin: 5px 0 0; color: var(--text-muted); line-height: 1.55; }
.section-icon { display: grid; width: 36px; height: 36px; flex: 0 0 auto; place-items: center; border-radius: 10px; background: var(--brand-soft); color: var(--brand); }
.setting-row { display: flex; min-height: 92px; align-items: center; justify-content: space-between; gap: 30px; border-top: 1px solid var(--border); }
.setting-row.secondary { min-height: 82px; }
.setting-row.disabled { opacity: .58; }
.setting-copy { min-width: 0; }
.setting-copy strong, .setting-copy span { display: block; }
.setting-copy strong { font-size: 14px; }
.setting-copy span { max-width: 590px; margin-top: 6px; color: var(--text-muted); line-height: 1.55; }
.switch-control { display: inline-flex; flex: 0 0 auto; align-items: center; gap: 9px; cursor: pointer; }
.switch-control input { position: absolute; width: 1px; height: 1px; opacity: 0; }
.switch-track { position: relative; width: 42px; height: 24px; border: 1px solid var(--border-strong); border-radius: 999px; background: var(--surface-subtle); transition: background .16s ease, border-color .16s ease; }
.switch-track span { position: absolute; top: 3px; left: 3px; width: 16px; height: 16px; border-radius: 50%; background: var(--text-muted); transition: transform .16s ease, background .16s ease; }
.switch-control input:checked + .switch-track { border-color: var(--brand); background: var(--brand); }
.switch-control input:checked + .switch-track span { transform: translateX(18px); background: #fff; }
.switch-control input:focus-visible + .switch-track { outline: 2px solid var(--focus); outline-offset: 2px; }
.switch-control input:disabled + .switch-track { cursor: not-allowed; }
.switch-label { min-width: 52px; color: var(--text-muted); font-size: 13px; }
.language-row { min-height: 76px; }
.language-select { min-width: 190px; min-height: 38px; padding: 0 32px 0 11px; border: 1px solid var(--border-strong); border-radius: var(--radius); background: var(--surface); color: var(--text); }
.feedback { display: flex; align-items: center; gap: 9px; margin-bottom: 16px; padding: 11px 13px; border-radius: var(--radius); }
.feedback span { flex: 1; }
.feedback.error { border: 1px solid #efc9c9; background: var(--danger-soft); color: var(--danger); }
.feedback.success { border: 1px solid #b9dfca; background: #eef9f2; color: #23633c; }
.loading-state { display: flex; align-items: center; gap: 8px; padding: 13px 0 18px; border-top: 1px solid var(--border); color: var(--text-muted); }
.information-panel { padding-bottom: 3px; }
.legal-panel { padding-bottom: 20px; }
.legal-copy { padding: 0 0 2px 48px; color: var(--text-muted); line-height: 1.6; }
.legal-copy p { margin: 0 0 10px; }
.legal-copy code { overflow-wrap: anywhere; color: var(--text); }
.license-text { max-height: 320px; overflow: auto; margin: 14px 0 0; padding: 14px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface-subtle); color: var(--text); font: 12px/1.5 ui-monospace, SFMono-Regular, Consolas, monospace; white-space: pre-wrap; }
.spin { animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
@media (max-width: 760px) {
  .setting-row { align-items: flex-start; flex-direction: column; gap: 14px; padding: 18px 0; }
  .switch-control { align-self: stretch; justify-content: flex-end; }
}
</style>
