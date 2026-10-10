import { mount } from "@vue/test-utils"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import MemoriesView from "@/views/MemoriesView.vue"

beforeEach(() => { window.location.hash = "#/memories" })
afterEach(() => vi.unstubAllGlobals())

describe("MemoriesView", () => {
  it("supports scanning, filtering and inspecting a memory revision", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({
          total: 3,
          items: [
            {
              id: "memory_01",
              type: "decision",
              statement: "首版优先支持 Windows。",
              reason: "先验证本地自动闭环。",
              namespace: "general",
              sensitivity: "personal",
              epistemic_state: "asserted",
              status: "active",
              revision: 2,
              payload: { source: "Windows 发布笔记", locator: "发布计划.md#Windows", evidence_start: 12, evidence_end: 34 },
              created_at: "2026-07-30T00:00:00Z",
              updated_at: "2026-07-30T00:01:00Z", quality_issues: [
                { code: "CROSS_NAMESPACE_EXACT_DUPLICATE", related_memory_id: "memory_02", related_namespace: "project" },
                { code: "MISSING_DECISION_RATIONALE" },
              ],
              review_assistance: {
                locale: "zh-CN",
                summary: "Windows 首版决策需要补充形成原因。",
                explanation: "英文原文描述了首版平台选择。",
                recommendation: "rewrite",
                recommendation_reason: "保留决策，但补齐数据主权与验证成本方面的理由。",
              },
              review_triage: {
                suggested_verdict: "rewrite", effective_verdict: "rewrite", confidence: "medium",
                reason_codes: ["MISSING_DECISION_RATIONALE"], requires_user_review: true,
                delivery_eligible: false, graph_eligible: false, scope_hint: null,
                overridden_by_user: false, memory_revision: 2,
              },
            },
            {
              id: "memory_02", type: "fact", statement: "Windows 已完成验证", reason: null,
              namespace: "general", sensitivity: "normal", epistemic_state: "inferred", status: "active", revision: 1,
              payload: { source_signal: "同步日志" }, created_at: "2026-07-30T00:00:00Z", updated_at: "2026-07-30T00:01:00Z",
              review_triage: {
                suggested_verdict: "source_only", effective_verdict: "source_only", confidence: "high",
                reason_codes: ["AGENT_WORKFLOW_NOTE", "UNVERIFIED_DETERMINISTIC_HIGH_IMPACT"], requires_user_review: false,
                delivery_eligible: false, graph_eligible: false, scope_hint: null,
                overridden_by_user: false, memory_revision: 1,
              },
            },
            {
              id: "memory_03", type: "goal", statement: "等待用户确认发布范围", reason: null,
              namespace: "general", sensitivity: "normal", epistemic_state: "uncertain", status: "active", revision: 1,
              payload: { source_format: "markdown" }, created_at: "2026-07-30T00:00:00Z", updated_at: "2026-07-30T00:01:00Z",
              review_triage: {
                suggested_verdict: "test_artifact", effective_verdict: "test_artifact", confidence: "high",
                reason_codes: ["TEST_SOURCE"], requires_user_review: false,
                delivery_eligible: false, graph_eligible: false, scope_hint: null,
                overridden_by_user: false, memory_revision: 1,
              },
            },
          ],
        }),
      }),
    )
    window.__PERSONALMEMORY__ = { endpoint: "http://127.0.0.1:19191", token: "token" }

    const wrapper = mount(MemoriesView)
    await vi.waitFor(() => expect(wrapper.text()).toContain("首版优先支持 Windows。"))

    expect(wrapper.find("input[type='search']").exists()).toBe(true)
    expect(wrapper.find("select[aria-label='记忆类型']").exists()).toBe(true)
    expect(wrapper.find("select[aria-label='治理状态']").exists()).toBe(true)
    expect((wrapper.get("select[aria-label='治理状态']").element as HTMLSelectElement).value).toBe("attention")
    expect(wrapper.text()).toContain("全部 3")
    expect(wrapper.text()).toContain("需要你决定 1")
    expect(wrapper.text()).toContain("系统已处理 2")
    expect(wrapper.text()).toContain("不交付给 Agent 3")
    await wrapper.get("button[data-memory-id='memory_01']").trigger("click")
    expect(wrapper.text()).toContain("Windows 首版决策需要补充形成原因。")
    expect(wrapper.text()).toContain("中文辅助理解")
    expect(wrapper.text()).toContain("建议提炼后保留")
    expect(wrapper.text()).toContain("不写入记忆")
    expect(wrapper.text()).toContain("先验证本地自动闭环。")
    expect(wrapper.text()).toContain("第 2 版")
    expect(wrapper.text()).toContain("Windows 发布笔记")
    expect(wrapper.text()).toContain("发布计划.md#Windows")
    expect(wrapper.text()).toContain("创建时间")
    expect(wrapper.text()).toContain("更新时间")
    expect(wrapper.text()).toContain("已确认")
    expect(wrapper.text()).toContain("其他适用范围中的内容完全重复")
    expect(wrapper.text()).toContain("相关适用范围：project")
    expect(wrapper.text()).toContain("这条决策缺少形成原因")
    expect(wrapper.find("button[aria-label='删除这条记忆']").exists()).toBe(true)
    expect(wrapper.find("header button[aria-label='纠正这条记忆']").exists()).toBe(true)
    expect(wrapper.find("footer button[aria-label='删除这条记忆']").exists()).toBe(false)
    expect(wrapper.find("button[data-memory-id='memory_01'].selected").exists()).toBe(true)
    expect(wrapper.get("[data-scroll-region='memory-list']").attributes("tabindex")).toBe("0")
    expect(wrapper.get("[data-scroll-region='memory-detail']").attributes("tabindex")).toBe("0")

    const listScroll = wrapper.get("[data-scroll-region='memory-list']").element as HTMLElement
    const detailScroll = wrapper.get("[data-scroll-region='memory-detail']").element as HTMLElement
    detailScroll.scrollTop = 180
    listScroll.scrollTop = 240
    await wrapper.get("[data-scroll-region='memory-list']").trigger("scroll")
    expect(detailScroll.scrollTop).toBe(180)

    await wrapper.get("header button[aria-label='纠正这条记忆']").trigger("click")
    expect(wrapper.find("button[aria-label='取消纠正']").exists()).toBe(true)
    expect(wrapper.find("button[aria-label='保存纠正']").exists()).toBe(true)
    await wrapper.get("button[aria-label='取消纠正']").trigger("click")
    expect(wrapper.find("button[aria-label='纠正这条记忆']").exists()).toBe(true)

    await wrapper.get("select[aria-label='治理状态']").setValue("handled")
    expect(wrapper.find("button[data-memory-id='memory_02']").exists()).toBe(true)
    expect(wrapper.find("button[data-memory-id='memory_01']").exists()).toBe(false)
    expect(wrapper.find("button[data-memory-id='memory_03']").exists()).toBe(true)
    await wrapper.get("button[data-memory-id='memory_02']").trigger("click")
    expect(detailScroll.scrollTop).toBe(0)
    expect(wrapper.text()).toContain("系统已自动处理")
    expect(wrapper.text()).toContain("不会进入 Agent 上下文")
    expect(wrapper.text()).toContain("不作为长期记忆")
    expect(wrapper.text()).toContain("属于 Agent 工作索引或运维备忘")
    expect(wrapper.text()).toContain("系统没有把这句话认定为你的真实身份或偏好")
    expect(wrapper.text()).toContain("调整系统判断")

    await wrapper.get("select[aria-label='治理状态']").setValue("attention")
    expect(wrapper.text()).toContain("只剩 1 条需要你的判断")
    expect(wrapper.text()).toContain("不要求你审核普通日志")
    expect(wrapper.find(".review-guide").text()).not.toContain("已有中文辅助")
    expect(wrapper.text()).toContain("保留为长期记忆")
    expect(wrapper.text()).toContain("项目经验，可跨项目联想")
    expect(wrapper.text()).toContain("只保留来源材料")
    expect(wrapper.text()).toContain("这是测试记录")
    expect(wrapper.text()).toContain("证据不足，稍后判断")
  })

  it("records a non-destructive gold-set verdict", async () => {
    const memory = {
      id: "memory_review", type: "preference", statement: "Do not guess without evidence.", reason: null,
      namespace: "codex", sensitivity: "personal", epistemic_state: "inferred", status: "active", revision: 1,
      payload: {}, created_at: "2026-08-05T00:00:00Z", updated_at: "2026-08-05T00:00:00Z",
      review_assistance: {
        locale: "zh-CN", summary: "没有证据时不要猜测。", explanation: "可能来自一次测试提示。",
        recommendation: "keep", recommendation_reason: "这条规则可能有长期价值。",
      },
      review_triage: {
        suggested_verdict: "needs_context", effective_verdict: "needs_context", confidence: "medium",
        reason_codes: ["UNVERIFIED_AGENT_HIGH_IMPACT"], requires_user_review: true,
        delivery_eligible: false, graph_eligible: false, scope_hint: null,
        overridden_by_user: false, memory_revision: 1,
      },
    }
    const fetchMock = vi.fn().mockImplementation((_input: string, init?: RequestInit) => {
      if (init?.method === "PATCH") {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({
            verdict: "needs_context",
            memory_revision: 1,
            reviewed_at: "2026-08-11T00:00:00Z",
            note: null,
            scope_hint: null,
          }),
        })
      }
      return Promise.resolve({ ok: true, status: 200, json: async () => ({ total: 1, items: [memory] }) })
    })
    vi.stubGlobal("fetch", fetchMock)
    window.__PERSONALMEMORY__ = { endpoint: "http://127.0.0.1:19191", token: "token" }

    const wrapper = mount(MemoriesView)
    await vi.waitFor(() => expect(wrapper.text()).toContain("没有证据时不要猜测。"))
    const decisionButton = wrapper.findAll("button").find((button) => button.text().includes("证据不足，稍后判断"))
    expect(decisionButton).toBeDefined()
    await decisionButton!.trigger("click")
    await vi.waitFor(() => expect(wrapper.text()).toContain("已记录“证据不足”，未修改或删除原记忆。"))

    const patchCall = fetchMock.mock.calls.find(([, init]) => init?.method === "PATCH")
    expect(patchCall?.[0]).toContain("/memories/memory_review/review")
    expect(JSON.parse(String(patchCall?.[1]?.body))).toEqual({
      expected_revision: 1,
      verdict: "needs_context",
      scope_hint: null,
    })
    expect(wrapper.text()).toContain("已按你的判断处理")
    expect(wrapper.text()).toContain("按你的判断处理")
    expect(wrapper.text()).toContain("Do not guess without evidence.")
  })

  it("opens the filtered memory requested by an overview deep link", async () => {
    window.location.hash = "#/memories?state=review&memory=memory_02"
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        total: 2,
        items: [
          { id: "memory_01", type: "decision", statement: "已确认决策", reason: null, namespace: "general", sensitivity: "normal", epistemic_state: "asserted", status: "active", revision: 1, payload: {}, created_at: "2026-07-30T00:00:00Z", updated_at: "2026-07-30T00:00:00Z" },
          { id: "memory_02", type: "insight", statement: "需要复核的洞察", reason: "来自系统推断", namespace: "general", sensitivity: "personal", epistemic_state: "inferred", status: "active", revision: 1, payload: { source: "deterministic" }, created_at: "2026-07-30T00:00:00Z", updated_at: "2026-07-30T00:00:00Z", review_triage: { suggested_verdict: "needs_context", effective_verdict: "needs_context", confidence: "medium", reason_codes: ["SENSITIVE_INFERENCE"], requires_user_review: true, delivery_eligible: false, graph_eligible: false, scope_hint: null, overridden_by_user: false, memory_revision: 1 } },
        ],
      }),
    }))
    window.__PERSONALMEMORY__ = { endpoint: "http://127.0.0.1:19191", token: "token" }

    const wrapper = mount(MemoriesView)
    await vi.waitFor(() => expect(wrapper.find("button[data-memory-id='memory_02'].selected").exists()).toBe(true))

    expect((wrapper.get("select[aria-label='治理状态']").element as HTMLSelectElement).value).toBe("attention")
    expect(wrapper.find("button[data-memory-id='memory_01']").exists()).toBe(false)
    expect(wrapper.text()).toContain("来自系统推断")
    expect(wrapper.text()).toContain("本地规则识别")
  })
})
