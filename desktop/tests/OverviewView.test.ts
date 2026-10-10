import { mount } from "@vue/test-utils"
import { createMemoryHistory, createRouter } from "vue-router"
import { afterEach, describe, expect, it, vi } from "vitest"

import OverviewView from "@/views/OverviewView.vue"

afterEach(() => vi.unstubAllGlobals())

describe("OverviewView", () => {
  it("summarizes memories and continuous sources instead of listing every connector", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url.endsWith("/health")) return { ok: true, status: 200, json: async () => ({ status: "ready", database: "ready", version: "0.2.2", build_revision: "stage47" }) }
      if (url.endsWith("/connectors")) return { ok: true, status: 200, json: async () => ({ items: [
        { id: "hook", name: "项目接入", profile_id: "claude_code_hook", source_path: "C:/project", target_path: null, namespace: "project", capabilities: { read: false, write: false }, read_enabled: false, write_enabled: false, enabled: true, status: "healthy" },
        { id: "import", name: "旧导入", profile_id: "codex_jsonl", source_path: "C:/sessions", target_path: null, namespace: "general", capabilities: { read: true, write: false }, read_enabled: true, write_enabled: false, enabled: true, status: "healthy" },
      ] }) }
      if (url.includes("/memories")) return { ok: true, status: 200, json: async () => ({ total: 2, items: [
        { id: "m1", type: "decision", statement: "Core 保持平台无关，并且这是一条需要在窄窗口中稳定截断而不能撑开页面的超长记忆内容", reason: null, namespace: "general", sensitivity: "personal", epistemic_state: "asserted", status: "active", revision: 1, payload: {}, created_at: "2026-08-04T00:00:00Z", updated_at: "2026-08-04T00:00:00Z", review_triage: { suggested_verdict: "keep", effective_verdict: "keep", confidence: "high", reason_codes: ["ASSERTED_CORE"], requires_user_review: false, delivery_eligible: true, graph_eligible: true, scope_hint: null, overridden_by_user: false, memory_revision: 1 } },
        { id: "m2", type: "fact", statement: "待检查的推断", reason: null, namespace: "general", sensitivity: "personal", epistemic_state: "inferred", status: "active", revision: 1, payload: {}, created_at: "2026-08-04T00:00:00Z", updated_at: "2026-08-04T00:00:00Z", review_triage: { suggested_verdict: "needs_context", effective_verdict: "needs_context", confidence: "medium", reason_codes: ["SENSITIVE_INFERENCE"], requires_user_review: true, delivery_eligible: false, graph_eligible: false, scope_hint: null, overridden_by_user: false, memory_revision: 1 } },
      ] }) }
      if (url.endsWith("/activities")) return { ok: true, status: 200, json: async () => ({ items: [] }) }
      return { ok: true, status: 200, json: async () => ({ items: [], total: 0 }) }
    }))
    window.__PERSONALMEMORY__ = { endpoint: "http://127.0.0.1:19191", token: "token" }
    const router = createRouter({ history: createMemoryHistory(), routes: [
      { path: "/", component: OverviewView },
      { path: "/memories", component: { template: "<div />" } },
      { path: "/connections", component: { template: "<div />" } },
      { path: "/exceptions", component: { template: "<div />" } },
      { path: "/activity", component: { template: "<div />" } },
    ] })
    await router.push("/")
    await router.isReady()

    const wrapper = mount(OverviewView, { global: { plugins: [router] } })
    await vi.waitFor(() => expect(wrapper.text()).toContain("Core 保持平台无关"))
    const recentStatement = wrapper.get(".memory-statement")
    expect(recentStatement.attributes("title")).toContain("不能撑开页面")
    expect(recentStatement.element.tagName).toBe("SPAN")
    expect(wrapper.text()).toContain("全部记忆")
    expect(wrapper.text()).toContain("系统已处理")
    expect(wrapper.text()).toContain("需要你决定")
    expect(wrapper.text()).toContain("待检查的推断")
    expect(wrapper.text()).toContain("Agent 接入")
    expect(wrapper.text()).toContain("不交付给 Agent")
    expect(wrapper.text()).toContain("PersonalMemory v0.2.2 · stage47")
    expect(wrapper.text()).not.toContain("旧导入")
  })
})
