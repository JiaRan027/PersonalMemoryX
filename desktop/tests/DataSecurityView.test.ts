import { mount } from "@vue/test-utils"
import { afterEach, describe, expect, it, vi } from "vitest"

import DataSecurityView from "@/views/DataSecurityView.vue"

const { open, save } = vi.hoisted(() => ({ open: vi.fn(), save: vi.fn() }))

vi.mock("@tauri-apps/plugin-dialog", () => ({ open, save }))

afterEach(() => {
  open.mockReset()
  save.mockReset()
  vi.unstubAllGlobals()
})

describe("DataSecurityView", () => {
  it("opens a native save dialog and displays both generated export files", async () => {
    save.mockResolvedValue("C:\\Users\\test-user\\Desktop\\my-memory")
    const fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        valid: true,
        memory_count: 4,
        memory_space_id: "space_default",
        package_path: "C:\\Users\\test-user\\Desktop\\personalmemory-20260804-152030.pmemory",
        readable_path: "C:\\Users\\test-user\\Desktop\\personalmemory-20260804-152030.md",
      }),
    })
    vi.stubGlobal("fetch", fetch)
    window.__PERSONALMEMORY__ = {
      endpoint: "http://127.0.0.1:19191",
      token: "test-token",
    }

    const wrapper = mount(DataSecurityView)
    await wrapper.get("[data-package-export]").trigger("click")

    await vi.waitFor(() =>
      expect(wrapper.text()).toContain("personalmemory-20260804-152030.pmemory"),
    )
    expect(wrapper.text()).toContain("personalmemory-20260804-152030.md")
    expect(save).toHaveBeenCalledWith(expect.objectContaining({
      title: "导出完整记忆",
      defaultPath: expect.stringMatching(/^personalmemory-\d{8}-\d{6}\.pmemory$/),
      filters: [{ name: "PersonalMemory 迁移包", extensions: ["pmemory"] }],
    }))
    expect(fetch).toHaveBeenCalledWith(
      "http://127.0.0.1:19191/api/v1/protocol/export",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ package_path: "C:\\Users\\test-user\\Desktop\\my-memory.pmemory" }),
      }),
    )
  })

  it("does nothing when the export save dialog is cancelled", async () => {
    save.mockResolvedValue(null)
    const fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ items: [], total: 0 }),
    })
    vi.stubGlobal("fetch", fetch)
    window.__PERSONALMEMORY__ = { endpoint: "http://127.0.0.1:19191", token: "test-token" }

    const wrapper = mount(DataSecurityView)
    await wrapper.get("[data-package-export]").trigger("click")
    await vi.waitFor(() => expect(save).toHaveBeenCalledOnce())

    expect(fetch).toHaveBeenCalledTimes(1)
    expect(wrapper.find("[role='alert']").exists()).toBe(false)
    expect(wrapper.find("[role='status']").exists()).toBe(false)
  })

  it("shows an explicit error and does not import an invalid package", async () => {
    open.mockResolvedValue("D:\\Backups\\invalid.pmemory")
    const fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ valid: false, memory_count: 0, memory_space_id: "space_default" }),
    })
    vi.stubGlobal("fetch", fetch)
    vi.spyOn(window, "confirm")
    window.__PERSONALMEMORY__ = { endpoint: "http://127.0.0.1:19191", token: "test-token" }

    const wrapper = mount(DataSecurityView)
    await wrapper.get("[data-package-import]").trigger("click")

    await vi.waitFor(() => expect(wrapper.get("[role='alert']").text()).toContain("未通过完整性验证"))
    expect(open).toHaveBeenCalledWith({
      title: "选择要导入的记忆备份",
      multiple: false,
      directory: false,
      filters: [{ name: "PersonalMemory 迁移包", extensions: ["pmemory"] }],
    })
    expect(window.confirm).not.toHaveBeenCalled()
    expect(fetch).toHaveBeenCalledTimes(2)
  })

  it("does nothing when the import file dialog is cancelled", async () => {
    open.mockResolvedValue(null)
    const fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ items: [], total: 0 }),
    })
    vi.stubGlobal("fetch", fetch)
    window.__PERSONALMEMORY__ = { endpoint: "http://127.0.0.1:19191", token: "test-token" }

    const wrapper = mount(DataSecurityView)
    await wrapper.get("[data-package-import]").trigger("click")
    await vi.waitFor(() => expect(open).toHaveBeenCalledOnce())

    expect(fetch).toHaveBeenCalledTimes(1)
    expect(wrapper.find("[role='alert']").exists()).toBe(false)
    expect(wrapper.find("[role='status']").exists()).toBe(false)
  })

  it("explains an unsupported package version without writing data", async () => {
    open.mockResolvedValue("D:\\Backups\\future.pmemory")
    const fetch = vi.fn().mockImplementation((url: string) => {
      if (url.endsWith("/memories/namespaces")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({ items: [], total: 0 }),
        })
      }
      return Promise.resolve({
        ok: false,
        status: 400,
        json: async () => ({ detail: { code: "PACKAGE_VERSION_UNSUPPORTED" } }),
      })
    })
    vi.stubGlobal("fetch", fetch)
    window.__PERSONALMEMORY__ = { endpoint: "http://127.0.0.1:19191", token: "test-token" }

    const wrapper = mount(DataSecurityView)
    await wrapper.get("[data-package-import]").trigger("click")

    await vi.waitFor(() => expect(wrapper.get("[role='alert']").text()).toContain("请先升级"))
    expect(fetch).toHaveBeenCalledTimes(2)
  })

  it("disables both package operations while exporting", async () => {
    save.mockResolvedValue("C:\\Users\\test-user\\Desktop\\backup.pmemory")
    let resolveExport: ((value: unknown) => void) | undefined
    const fetch = vi.fn().mockReturnValue(new Promise((resolve) => { resolveExport = resolve }))
    vi.stubGlobal("fetch", fetch)
    window.__PERSONALMEMORY__ = { endpoint: "http://127.0.0.1:19191", token: "test-token" }

    const wrapper = mount(DataSecurityView)
    await wrapper.get("[data-package-export]").trigger("click")

    expect(wrapper.get("[data-package-export]").attributes("disabled")).toBeDefined()
    expect(wrapper.get("[data-package-import]").attributes("disabled")).toBeDefined()
    expect(wrapper.text()).toContain("正在导出...")

    resolveExport?.({ ok: true, status: 200, json: async () => ({ memory_count: 1, package_path: "backup.pmemory", readable_path: "backup.md" }) })
    await vi.waitFor(() => expect(wrapper.text()).toContain("backup.pmemory"))
  })

  it("builds a visible governed Capsule and copies the exact delivery text", async () => {
    const clipboard = { writeText: vi.fn().mockResolvedValue(undefined) }
    vi.stubGlobal("navigator", { clipboard })
    const response = {
      capsule: {
        schema_version: "resume-capsule/0",
        capsule_id: "capsule_11111111111111111111111111111111",
        generated_at: "2026-08-05T04:00:00Z",
        expires_at: "2026-08-05T05:00:00Z",
        ttl_seconds: 3600,
        namespace_scope: ["projects/personalmemory"],
        max_sensitivity: "personal",
        source_digest: "a".repeat(64),
        selection: { query: "continue source governance", namespaces: ["projects/personalmemory"], allowed_types: ["decision"], max_sensitivity: "personal", limit: 10 },
        memory_references: [{ memory_id: "memory_1", revision: 2, type: "decision", statement: "Keep the Continuity Core platform-neutral.", reason: "Agents are replaceable.", namespace: "projects/personalmemory", sensitivity: "personal" }],
        checkpoint: { current_task: "Source governance", verified_status: "Stage 46 PASS", next_action: "Show source scope" },
        dropped_fields: [],
        token_budget: 1200,
        estimated_tokens: 603,
      },
      delivery_text: "# Continuity Capsule\nKeep the Continuity Core platform-neutral.",
    }
    const fetch = vi.fn().mockImplementation((url: string) => {
      if (url.endsWith("/memories/namespaces")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({
            items: [
              { namespace: "projects/personalmemory", memory_count: 12 },
              { namespace: "codex", memory_count: 5 },
            ],
            total: 17,
          }),
        })
      }
      return Promise.resolve({ ok: true, status: 200, json: async () => response })
    })
    vi.stubGlobal("fetch", fetch)
    window.__PERSONALMEMORY__ = { endpoint: "http://127.0.0.1:19191", token: "test-token" }

    const wrapper = mount(DataSecurityView)
    await vi.waitFor(() => expect(wrapper.text()).toContain("全部范围（17 条）"))
    await wrapper.get("[data-capsule-query]").setValue("continue source governance")
    await wrapper.get('[data-namespace="projects/personalmemory"]').setValue(true)
    await wrapper.get("[data-current-task]").setValue("Source governance")
    await wrapper.get("[data-verified-status]").setValue("Stage 46 PASS")
    await wrapper.get("[data-next-action]").setValue("Show source scope")
    await wrapper.findAll("button").find((button) => button.text().includes("生成并预览"))?.trigger("click")

    await vi.waitFor(() => expect(wrapper.text()).toContain("Keep the Continuity Core platform-neutral."))
    expect(wrapper.text()).toContain("603 / 1200 tokens")
    expect(wrapper.text()).toContain("1 条受管引用")
    expect(wrapper.text()).toContain("revision 2")
    expect(wrapper.text()).toContain("Secret 已排除")
    expect(fetch).toHaveBeenCalledWith(
      "http://127.0.0.1:19191/api/v1/capsules",
      expect.objectContaining({
        method: "POST",
        body: expect.stringContaining('"namespaces":["projects/personalmemory"]'),
      }),
    )

    await wrapper.findAll("button").find((button) => button.text().includes("复制给 Agent"))?.trigger("click")
    expect(clipboard.writeText).toHaveBeenCalledWith(response.delivery_text)
  })

  it("validates, rebuilds with refreshed TTL, and exports the current Capsule", async () => {
    save.mockResolvedValue("C:\\Desktop\\personalmemory-capsule")
    const capsule = {
      schema_version: "resume-capsule/0",
      capsule_id: "capsule_11111111111111111111111111111111",
      generated_at: "2026-08-05T04:00:00Z",
      expires_at: "2026-08-05T05:00:00Z",
      ttl_seconds: 3600,
      namespace_scope: [],
      max_sensitivity: "personal",
      source_digest: "a".repeat(64),
      selection: { query: "中文任务", namespaces: null, allowed_types: ["decision"], max_sensitivity: "personal", limit: 10 },
      memory_references: [],
      checkpoint: { current_task: "中文任务", verified_status: null, next_action: null },
      dropped_fields: [],
      token_budget: 1200,
      estimated_tokens: 120,
    }
    const rebuiltCapsule = { ...capsule, capsule_id: "capsule_22222222222222222222222222222222", ttl_seconds: 86400, expires_at: "2026-08-06T04:00:00Z" }
    const built = { capsule, delivery_text: "Resume Capsule v0: original\n中文任务" }
    const rebuilt = { capsule: rebuiltCapsule, delivery_text: "Resume Capsule v0: rebuilt\n中文任务" }
    const valid = { valid: true, expired: false, stale: false, checked_at: "2026-08-05T04:01:00Z", issues: [] }
    const fetch = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      if (url.endsWith("/memories/namespaces")) {
        return Promise.resolve({ ok: true, status: 200, json: async () => ({ items: [], total: 0 }) })
      }
      if (url.endsWith("/capsules") && !url.endsWith("/validate") && !url.endsWith("/rebuild") && !url.endsWith("/export")) {
        return Promise.resolve({ ok: true, status: 200, json: async () => built })
      }
      if (url.endsWith("/capsules/validate")) {
        return Promise.resolve({ ok: true, status: 200, json: async () => valid })
      }
      if (url.endsWith("/capsules/rebuild")) {
        return Promise.resolve({ ok: true, status: 200, json: async () => rebuilt })
      }
      if (url.endsWith("/capsules/export")) {
        return Promise.resolve({ ok: true, status: 200, json: async () => ({ path: "C:\\Desktop\\personalmemory-capsule.md", bytes_written: 512, capsule_id: rebuiltCapsule.capsule_id, delivery_text: rebuilt.delivery_text, validation: valid }) })
      }
      return Promise.resolve({ ok: true, status: 200, json: async () => ({}) })
    })
    vi.stubGlobal("fetch", fetch)
    window.__PERSONALMEMORY__ = { endpoint: "http://127.0.0.1:19191", token: "test-token" }

    const wrapper = mount(DataSecurityView)
    await wrapper.get("[data-capsule-query]").setValue("中文任务")
    await wrapper.get("[data-capsule-build]").trigger("click")
    await vi.waitFor(() => expect(wrapper.get("[data-capsule-validation]").text()).toContain("验证通过"))
    expect(wrapper.get("[data-capsule-validation]").text()).toContain("expired=false")

    await wrapper.get("[data-capsule-rebuild]").trigger("click")
    await vi.waitFor(() => expect(wrapper.text()).toContain("已按当前 Memory Core 重建 Capsule"))
    expect(wrapper.text()).toContain("rebuilt")
    await wrapper.get("[data-capsule-export]").trigger("click")
    await vi.waitFor(() => expect(wrapper.text()).toContain("personalmemory-capsule.md"))

    expect(fetch).toHaveBeenCalledWith(
      "http://127.0.0.1:19191/api/v1/capsules/validate",
      expect.objectContaining({ method: "POST" }),
    )
    expect(fetch).toHaveBeenCalledWith(
      "http://127.0.0.1:19191/api/v1/capsules/rebuild",
      expect.objectContaining({ method: "POST", body: expect.stringContaining('"ttl_seconds":3600') }),
    )
    expect(fetch).toHaveBeenCalledWith(
      "http://127.0.0.1:19191/api/v1/capsules/export",
      expect.objectContaining({ method: "POST", body: expect.stringContaining('"path":"C:\\\\Desktop\\\\personalmemory-capsule.md"') }),
    )
    expect(save).toHaveBeenCalledWith(expect.objectContaining({
      title: "保存 Continuity Capsule",
      defaultPath: expect.stringMatching(/^personalmemory-capsule-\d{8}-\d{6}\.md$/),
      filters: [{ name: "Markdown 文件", extensions: ["md"] }],
    }))
  })
})
