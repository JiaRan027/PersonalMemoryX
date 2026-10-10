import { flushPromises, mount } from "@vue/test-utils"
import { beforeEach, describe, expect, it, vi } from "vitest"

import RecognitionView from "@/views/RecognitionView.vue"

const recognizeSources = vi.fn()
const extractPreview = vi.fn()
const importSource = vi.fn()

vi.mock("@/api/client", () => ({
  requireClient: () => ({ recognizeSources, extractPreview, importSource }),
}))

const supportedSource = {
  agent: "codex", kind: "memory", format: "markdown", path: "C:/Users/me/.codex/memory.md",
  size_bytes: 1024, file_count: 1, last_modified: null, priority: 1, note: "",
}

function mountView() {
  return mount(RecognitionView, { global: { stubs: { RouterLink: { template: "<a><slot /></a>" } } } })
}

describe("RecognitionView", () => {
  beforeEach(() => {
    recognizeSources.mockReset()
    extractPreview.mockReset()
    importSource.mockReset()
  })

  it("puts unsupported sources in a collapsed secondary section without actions", async () => {
    recognizeSources.mockResolvedValue({ items: [{ ...supportedSource, agent: "unknown_agent", format: "xml", path: "C:/data/export.xml" }] })
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.find("details.unsupported-sources").attributes("open")).toBeUndefined()
    expect(wrapper.text()).toContain("当前不能预览或导入")
    expect(wrapper.findAll("button").map((button) => button.text())).not.toContain("导入")
    const bulkButton = wrapper.findAll("button").find((button) => button.text().includes("导入可用来源"))
    expect(bulkButton?.text()).toContain("导入可用来源（0）")
    expect(bulkButton?.attributes("disabled")).toBeDefined()
  })

  it("shows preview ownership and extraction evidence", async () => {
    recognizeSources.mockResolvedValue({ items: [supportedSource] })
    extractPreview.mockResolvedValue({
      items: [{ type: "preference", statement: "Use Chinese", reason: "Explicit preference", confidence: 0.91, importance: 0.8, evidence: { start: 12, end: 44 }, source_signal: "user_message" }],
      warnings: [],
    })
    const wrapper = mountView()
    await flushPromises()
    await wrapper.get(".text-button").trigger("click")
    await flushPromises()

    expect(wrapper.text()).toContain("候选记忆")
    expect(wrapper.text()).toContain("目前尚未写入记忆库")
    expect(wrapper.text()).toContain("Codex · 记忆文件")
    expect(wrapper.text()).toContain("C:/Users/me/.codex/memory.md")
    expect(wrapper.text()).toContain("原文位置")
    expect(wrapper.text()).toContain("12–44")
    expect(wrapper.text()).toContain("识别方式")
    expect(wrapper.text()).toContain("用户明确表达")
    expect(wrapper.text()).toContain("长期价值")
    expect(wrapper.text()).toContain("80%")
    expect(wrapper.text()).toContain("导入后，每条候选还会经过重复、冲突和可信度检查")
  })

  it("shows an explicit empty preview result", async () => {
    recognizeSources.mockResolvedValue({ items: [supportedSource] })
    extractPreview.mockResolvedValue({ items: [], warnings: [] })
    const wrapper = mountView()
    await flushPromises()
    await wrapper.get(".text-button").trigger("click")
    await flushPromises()

    expect(wrapper.text()).toContain("候选记忆")
    expect(wrapper.text()).toContain("该来源未提取出候选记忆")
  })

  it("counts only supported sources in the bulk import button", async () => {
    recognizeSources.mockResolvedValue({ items: [supportedSource, { ...supportedSource, agent: "unknown_agent", format: "xml", path: "C:/data/export.xml" }] })
    const wrapper = mountView()
    await flushPromises()

    const bulkButton = wrapper.findAll("button").find((button) => button.text().includes("导入可用来源"))
    expect(bulkButton?.text()).toContain("导入可用来源（1）")
    expect(bulkButton?.attributes("disabled")).toBeUndefined()
  })
})

describe("RecognitionView admission quality", () => {
  it("explains a technical rule reclassification before import", async () => {
    recognizeSources.mockResolvedValue({ items: [supportedSource] })
    extractPreview.mockResolvedValue({ items: [{ type: "insight", statement: "Use TypeScript", reason: null, confidence: 0.9, importance: 0.8, evidence: { start: 1, end: 2 }, source_signal: "user_message", admission_action: "provisional", admission_reason_code: "UNCONFIRMED_OBSERVATION", extra: { classification_reason_code: "TECHNICAL_RULE_NOT_USER_PREFERENCE" } }], warnings: [] })
    const wrapper = mountView()
    await flushPromises()
    await wrapper.get(".text-button").trigger("click")
    await flushPromises()

    expect(wrapper.text()).toContain("洞察")
    expect(wrapper.text()).toContain("分类说明")
    expect(wrapper.text()).toContain("准入结果：需确认")
    expect(wrapper.text()).toContain("尚未确认的观察")
    expect(wrapper.text()).toContain("技术实现规则")
    expect(wrapper.text()).toContain("重新分类为洞察")
  })

  it("renders a discarded candidate as not imported", async () => {
    recognizeSources.mockResolvedValue({ items: [supportedSource] })
    extractPreview.mockResolvedValue({ items: [{ type: "fact", statement: "测试用户想了解示例应用的运行机制", reason: null, confidence: 0.9, importance: 0.5, evidence: { start: 1, end: 20 }, source_signal: "deterministic", admission_action: "discard", admission_reason_code: "TRANSIENT_INFORMATION_REQUEST" }], warnings: [] })
    const wrapper = mountView()
    await flushPromises()
    await wrapper.get(".text-button").trigger("click")
    await flushPromises()

    expect(wrapper.text()).toContain("准入结果：不会导入")
    expect(wrapper.text()).toContain("这是一次临时信息请求")
  })
})
