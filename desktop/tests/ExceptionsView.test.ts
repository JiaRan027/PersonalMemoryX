import { flushPromises, mount } from "@vue/test-utils"
import { beforeEach, describe, expect, it, vi } from "vitest"

import ExceptionsView from "@/views/ExceptionsView.vue"

const exceptions = vi.fn()

vi.mock("@/api/client", () => ({
  requireClient: () => ({ exceptions }),
}))

describe("ExceptionsView", () => {
  beforeEach(() => exceptions.mockReset())

  it("explains an actionable projection conflict without exposing file contents", async () => {
    exceptions.mockResolvedValue({
      total: 1,
      items: [
        {
          id: "issue_01",
          kind: "connector",
          reason_code: "PROJECTION_CONFLICT",
          statement: "Windows acceptance connector",
          memory_type: null,
          connector_id: "connector_01",
          created_at: "2026-07-30T04:07:39Z",
        },
      ],
    })

    const wrapper = mount(ExceptionsView)
    await flushPromises()

    expect(wrapper.text()).toContain("Windows acceptance connector")
    expect(wrapper.text()).toContain("目标文件已被外部修改，PersonalMemory 已停止写入")
  })

  it("shows a retryable failure instead of the empty-success state", async () => {
    exceptions
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValueOnce({ total: 0, items: [] })

    const wrapper = mount(ExceptionsView)
    await flushPromises()

    expect(wrapper.find("[role='alert']").exists()).toBe(true)
    expect(wrapper.get("button").text()).toBe("\u91cd\u8bd5")
    expect(wrapper.find(".empty").exists()).toBe(false)

    await wrapper.get("button").trigger("click")
    await flushPromises()

    expect(exceptions).toHaveBeenCalledTimes(2)
    expect(wrapper.find(".empty").exists()).toBe(true)
  })
})

describe("ExceptionsView memory quality", () => {
  it("explains a cross-namespace duplicate and links to its memory", async () => {
    exceptions.mockResolvedValue({ total: 1, items: [{ id: "issue_duplicate", kind: "memory_quality", reason_code: "CROSS_NAMESPACE_EXACT_DUPLICATE", statement: "Keep local notes", memory_type: "preference", memory_id: "memory_01", related_memory_ids: ["memory_02"], related_namespaces: ["general", "project"], created_at: "2026-08-05T00:00:00Z" }] })
    const wrapper = mount(ExceptionsView)
    await flushPromises()

    expect(wrapper.text()).toContain("记忆在不同适用范围中重复")
    expect(wrapper.text()).toContain("general、project")
    expect(wrapper.get("a.memory-link").attributes("href")).toBe("#/memories?memory=memory_01")
  })
})
