import { mount } from "@vue/test-utils"
import { afterEach, describe, expect, it, vi } from "vitest"

import ActivityView from "@/views/ActivityView.vue"

const activity = {
  id: "activity_1",
  operation_id: "operation_1",
  connector_id: "connector_1",
  kind: "source_ingested",
  status: "completed",
  summary: "自动处理 1 条候选记忆",
  details: { accepted: 1, provisional: 0, quarantined: 0, projected: 0, memory_id: "memory_1", revision: 2, unexpected: "kept", memory_changes: [{ id: "memory_1", action: "created", type: "decision", statement: "采用本地优先架构" }] },
  created_at: "2026-07-31T00:00:00Z",
}

function setupFetch(options: { activities?: unknown[]; failActivities?: boolean; undoFails?: boolean; refreshFails?: boolean } = {}) {
  let activityCalls = 0
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (String(url).includes("/undo")) {
      if (options.undoFails) throw new Error("projection conflict")
      return { ok: true, status: 200, json: async () => ({ undone: 1, operation_id: "operation_1" }) }
    }
    if (String(url).includes("/connectors")) {
      return { ok: true, status: 200, json: async () => ({ items: [{ id: "connector_1", name: "工作笔记", profile_id: "markdown", source_path: null, target_path: null, namespace: "default", capabilities: { read: true, write: false }, read_enabled: true, write_enabled: false, enabled: true, status: "healthy" }] }) }
    }
    activityCalls += 1
    if (options.failActivities || (options.refreshFails && activityCalls > 1)) throw new Error("temporary load failure")
    return { ok: true, status: 200, json: async () => ({ items: options.activities ?? [activity] }) }
  }))
  window.__PERSONALMEMORY__ = { endpoint: "http://127.0.0.1:19191", token: "token" }
}

afterEach(() => vi.unstubAllGlobals())

describe("ActivityView", () => {
  it("shows the source, readable result, and expandable known and unknown details", async () => {
    setupFetch()
    const wrapper = mount(ActivityView)
    await vi.waitFor(() => expect(wrapper.text()).toContain("工作笔记"))

    expect(wrapper.text()).toContain("来源导入")
    expect(wrapper.text()).toContain("已完成")
    expect(wrapper.text()).toContain("已接纳 1")
    expect(wrapper.text()).toContain("新增 · 决策")
    expect(wrapper.text()).toContain("采用本地优先架构")
    await wrapper.get("summary").trigger("click")
    expect(wrapper.text()).toContain("记忆 ID")
    expect(wrapper.text()).toContain("修订版本")
    expect(wrapper.text()).toContain("技术字段（unexpected）")
  })

  it("shows a genuine empty state only after a successful empty response", async () => {
    setupFetch({ activities: [] })
    const wrapper = mount(ActivityView)
    await vi.waitFor(() => expect(wrapper.text()).toContain("还没有活动记录"))
    expect(wrapper.text()).not.toContain("暂时无法读取活动记录")
  })

  it("renders a bounded activity list and expands it on demand", async () => {
    setupFetch({ activities: Array.from({ length: 35 }, (_, index) => ({
      ...activity,
      id: `activity_${index}`,
      operation_id: `operation_${index}`,
      summary: `activity summary ${index}`,
    })) })
    const wrapper = mount(ActivityView)
    await vi.waitFor(() => expect(wrapper.text()).toContain("显示 35 / 35 条活动"))

    expect(wrapper.findAll(".activity-row")).toHaveLength(30)
    await wrapper.get(".load-more button").trigger("click")
    expect(wrapper.findAll(".activity-row")).toHaveLength(35)
  })

  it("hides source checks that made no memory change by default but keeps the full audit accessible", async () => {
    setupFetch({ activities: [{
      ...activity,
      details: { accepted: 0, provisional: 1, quarantined: 0, memory_changes: [] },
    }] })
    const wrapper = mount(ActivityView)
    await vi.waitFor(() => expect(wrapper.text()).toContain("显示 0 / 1 条活动"))
    expect(wrapper.text()).toContain("没有实际记忆变化")
    expect(wrapper.findAll(".activity-row")).toHaveLength(0)

    await wrapper.get("input[type='checkbox']").setValue(false)
    await vi.waitFor(() => expect(wrapper.findAll(".activity-row")).toHaveLength(1))
    expect(wrapper.text()).toContain("处理 1 条候选 · 未写入正式记忆")
  })

  it("shows a load failure instead of an empty state", async () => {
    setupFetch({ failActivities: true })
    const wrapper = mount(ActivityView)
    await vi.waitFor(() => expect(wrapper.get("[role='alert']").text()).toContain("暂时无法读取活动记录"))
    expect(wrapper.text()).not.toContain("还没有活动记录")
  })

  it("labels undo as undoing this import and reports a refresh failure after success", async () => {
    setupFetch({ refreshFails: true })
    const wrapper = mount(ActivityView)
    await vi.waitFor(() => expect(wrapper.find("button[aria-label='撤销本次导入']").exists()).toBe(true))
    await wrapper.get("button[aria-label='撤销本次导入']").trigger("click")
    await vi.waitFor(() => expect(wrapper.text()).toContain("撤销已完成，但刷新活动记录时失败"))
    expect(wrapper.text()).not.toContain("本次导入无法撤销")
  })

  it("reports an undo failure when the API rejects the undo", async () => {
    setupFetch({ undoFails: true })
    const wrapper = mount(ActivityView)
    await vi.waitFor(() => expect(wrapper.find("button[aria-label='撤销本次导入']").exists()).toBe(true))
    await wrapper.get("button[aria-label='撤销本次导入']").trigger("click")
    await vi.waitFor(() => expect(wrapper.text()).toContain("本次导入无法撤销"))
  })
})
