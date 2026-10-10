import { mount } from "@vue/test-utils"
import { createPinia } from "pinia"
import { afterEach, describe, expect, it, vi } from "vitest"

import ConnectionsView from "@/views/ConnectionsView.vue"

const invoke = vi.hoisted(() => vi.fn())

vi.mock("@tauri-apps/api/core", () => ({ invoke }))

afterEach(() => {
  vi.unstubAllGlobals()
  invoke.mockReset()
})

function mountView() {
  return mount(ConnectionsView, {
    global: {
      plugins: [createPinia()],
      stubs: { RouterLink: { template: "<a><slot /></a>" } },
    },
  })
}

describe("ConnectionsView", () => {
  it("offers MCP command and JSON without exposing the session token", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({ items: [] }) }))
    const clipboard = { writeText: vi.fn().mockResolvedValue(undefined) }
    vi.stubGlobal("navigator", { clipboard })
    window.__PERSONALMEMORY__ = {
      endpoint: "http://127.0.0.1:19191",
      token: "must-not-be-copied",
      mcpCommand: "personalmemory-mcp",
      mcpJson: '{"mcpServers":{"personalmemory":{"command":"personalmemory-mcp","args":[]}}}',
    }

    const wrapper = mountView()
    await vi.waitFor(() => expect(wrapper.text()).toContain("连接其他支持 MCP 的 Agent"))
    expect(wrapper.text()).toContain("personalmemory-mcp")
    expect(wrapper.text()).not.toContain("must-not-be-copied")

    await wrapper.findAll("button").find((button) => button.text().includes("复制命令"))?.trigger("click")
    expect(clipboard.writeText).toHaveBeenCalledWith("personalmemory-mcp")
    await wrapper.findAll("button").find((button) => button.text().includes("复制 JSON"))?.trigger("click")
    expect(clipboard.writeText).toHaveBeenCalledWith(expect.stringContaining("mcpServers"))
  })

  it("enables global Claude continuity with selective long-term capture", async () => {
    const connector = {
      id: "connector_global",
      name: "Claude Code 个人连续性",
      profile_id: "claude_code_global_context",
      source_path: "C:/Users/demo",
      target_path: null,
      namespace: "general",
      capabilities: { read: true, write: false },
      read_enabled: true,
      write_enabled: false,
      enabled: true,
      status: "healthy",
    }
    let installed = false
    let listed = false
    const requests: Array<{ url: string; init?: RequestInit }> = []
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      requests.push({ url: String(url), init })
      if (init?.method === "POST" && String(url).endsWith("/connectors")) {
        listed = true
        expect(JSON.parse(String(init.body))).toMatchObject({
          profile_id: "claude_code_global_context",
          source_path: "C:/Users/demo",
          namespace: "general",
          read_enabled: true,
          write_enabled: false,
        })
        return { ok: true, status: 201, json: async () => connector }
      }
      if (String(url).endsWith("/activities")) return { ok: true, status: 200, json: async () => ({ items: [] }) }
      if (String(url).endsWith("/health")) return { ok: true, status: 200, json: async () => ({ status: "ready" }) }
      return { ok: true, status: 200, json: async () => ({ items: listed ? [connector] : [] }) }
    }))
    invoke.mockImplementation(async (command: string, payload?: { connectorId?: string }) => {
      if (command === "get_claude_global_integration") {
        return {
          rootPath: "C:/Users/demo",
          settingsPath: "C:/Users/demo/.claude/settings.json",
          mcpPath: "C:/Users/demo/.claude.json",
          settingsInstalled: installed,
          mcpInstalled: installed,
          mcpConflict: false,
        }
      }
      if (command === "install_claude_global_integration") {
        expect(payload).toEqual({ connectorId: "connector_global" })
        installed = true
        return {}
      }
      return {}
    })
    window.__PERSONALMEMORY__ = { endpoint: "http://127.0.0.1:19191", token: "token" }

    const wrapper = mountView()
    await vi.waitFor(() => expect(wrapper.text()).toContain("让 Claude 在你的个人目录中认识你"))
    expect(wrapper.text()).toContain("只沉淀明确的个人身份、长期目标、决定和偏好")
    expect(wrapper.text()).toContain("不保存普通提问或完整聊天")
    expect(wrapper.text()).toContain("未接入")
    await wrapper.findAll("button").find((button) => button.text().includes("接入 Claude Code"))?.trigger("click")

    await vi.waitFor(() => expect(wrapper.text()).toContain("已接入"))
    expect(invoke).toHaveBeenCalledWith("install_claude_global_integration", { connectorId: "connector_global" })
    expect(requests.some((request) => request.url.includes("/sync"))).toBe(false)
  })

  it("installs Codex recall and selective automatic capture as one connection", async () => {
    const connector = {
      id: "connector_codex",
      name: "Codex 个人连续性",
      profile_id: "codex_global_context",
      source_path: "C:/Users/demo",
      target_path: null,
      namespace: "general",
      capabilities: { read: true, write: false },
      read_enabled: true,
      write_enabled: false,
      enabled: true,
      status: "healthy",
    }
    let listed = false
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      if (String(url).endsWith("/health")) return { ok: true, status: 200, json: async () => ({ status: "ready" }) }
      if (init?.method === "POST" && String(url).endsWith("/connectors")) {
        listed = true
        expect(JSON.parse(String(init.body))).toMatchObject({
          profile_id: "codex_global_context",
          source_path: "C:/Users/demo",
          namespace: "general",
        })
        return { ok: true, status: 201, json: async () => connector }
      }
      if (String(url).endsWith("/activities")) return { ok: true, status: 200, json: async () => ({ items: [
        { connector_id: "connector_codex", kind: "source_ingested" },
      ] }) }
      return { ok: true, status: 200, json: async () => ({ items: listed ? [connector] : [] }) }
    }))
    let installed = false
    invoke.mockImplementation(async (command: string, payload?: { connectorId?: string }) => {
      if (command === "get_codex_integration") {
        return {
          rootPath: "C:/Users/demo",
          configPath: "C:/Users/demo/.codex/config.toml",
          guidancePath: "C:/Users/demo/.codex/AGENTS.md",
          hooksPath: "C:/Users/demo/.codex/hooks.json",
          mcpInstalled: installed,
          guidanceInstalled: installed,
          hooksInstalled: installed,
          installed,
          conflict: false,
        }
      }
      if (command === "install_codex_integration") {
        expect(payload).toEqual({ connectorId: "connector_codex" })
        installed = true
        return {
          rootPath: "C:/Users/demo",
          configPath: "C:/Users/demo/.codex/config.toml",
          guidancePath: "C:/Users/demo/.codex/AGENTS.md",
          hooksPath: "C:/Users/demo/.codex/hooks.json",
          mcpInstalled: true,
          guidanceInstalled: true,
          hooksInstalled: true,
          installed: true,
          conflict: false,
        }
      }
      if (command === "get_claude_global_integration") throw new Error("not available")
      return {}
    })
    window.__PERSONALMEMORY__ = { endpoint: "http://127.0.0.1:19191", token: "token" }

    const wrapper = mountView()
    await vi.waitFor(() => expect(wrapper.text()).toContain("让 Codex 使用 PersonalMemory"))
    expect(wrapper.text()).toContain("主动读取相关记忆")
    expect(wrapper.text()).toContain("选择性沉淀明确的个人身份、长期目标、决定和偏好")
    expect(wrapper.text()).toContain("普通提问和完整聊天不会保存")
    expect(wrapper.text()).toContain("未接入")
    await wrapper.findAll("button").find((button) => button.text().includes("接入 Codex"))?.trigger("click")

    await vi.waitFor(() => expect(wrapper.text()).toContain("Codex 读取、主动召回与自动记录已配置"))
    expect(wrapper.text()).toContain("Codex 设置 → Hooks")
    expect(wrapper.text()).toContain("两条 PersonalMemory 用户 Hook")
    await vi.waitFor(() => expect(wrapper.text()).toContain("记忆读取已验证 · 尚未收到 Hook 事件"))
    expect(wrapper.text()).toContain("如何确认 Codex 接入正常")
    expect(wrapper.text()).toContain("审核之后新建一个 Codex 任务，发送普通消息并等回复完成")
    expect(wrapper.text()).toContain("尚未收到 Codex Hook 事件")
    expect(wrapper.text()).toContain("PersonalMemory 读不到 Codex 的审核状态")
    expect(invoke).toHaveBeenCalledWith("install_codex_integration", { connectorId: "connector_codex" })
  })

  it("explains and enables only the missing Codex active recall capability", async () => {
    const connector = {
      id: "connector_codex",
      profile_id: "codex_global_context",
      source_path: "C:/Users/demo",
      enabled: true,
      read_enabled: true,
    }
    vi.stubGlobal("fetch", vi.fn(async (url: string) => ({
      ok: true,
      status: 200,
      json: async () => String(url).endsWith("/activities") ? { items: [] } : { items: [connector] },
    })))
    let guidanceInstalled = false
    invoke.mockImplementation(async (command: string) => {
      if (command === "get_codex_integration") {
        return {
          rootPath: "C:/Users/demo",
          configPath: "C:/Users/demo/.codex/config.toml",
          guidancePath: "C:/Users/demo/.codex/AGENTS.md",
          hooksPath: "C:/Users/demo/.codex/hooks.json",
          mcpInstalled: true,
          guidanceInstalled,
          hooksInstalled: true,
          installed: guidanceInstalled,
          conflict: false,
        }
      }
      if (command === "enable_codex_active_recall") {
        guidanceInstalled = true
        return {
          rootPath: "C:/Users/demo",
          configPath: "C:/Users/demo/.codex/config.toml",
          guidancePath: "C:/Users/demo/.codex/AGENTS.md",
          hooksPath: "C:/Users/demo/.codex/hooks.json",
          mcpInstalled: true,
          guidanceInstalled: true,
          hooksInstalled: true,
          installed: true,
          conflict: false,
        }
      }
      if (command === "get_claude_global_integration") throw new Error("not available")
      return {}
    })
    window.__PERSONALMEMORY__ = { endpoint: "http://127.0.0.1:19191", token: "token" }

    const wrapper = mountView()
    await vi.waitFor(() => expect(wrapper.text()).toContain("让 Codex 使用 PersonalMemory"))
    expect(wrapper.text()).toContain("主动召回未启用")
    expect(wrapper.text()).toContain("读取和自动记录已配置")
    expect(wrapper.text()).not.toContain("修复接入")

    await wrapper.findAll("button").find((button) => button.text().includes("启用主动召回"))?.trigger("click")

    await vi.waitFor(() => expect(wrapper.text()).toContain("Codex 主动召回已启用"))
    expect(invoke).toHaveBeenCalledWith("enable_codex_active_recall")
    expect(invoke).not.toHaveBeenCalledWith("install_codex_integration")
  })

  it("automatically verifies MCP and polls Hook receipts without manual clicks", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] })
    const connector = {
      id: "connector_codex",
      profile_id: "codex_global_context",
      source_path: "C:/Users/demo",
      enabled: true,
      read_enabled: true,
      hook_receipt: { user_message: "2026-10-09T14:00:00Z", assistant_message: "" },
    }
    vi.stubGlobal("fetch", vi.fn(async (url: string) => ({
      ok: true,
      status: 200,
      json: async () => String(url).endsWith("/health") ? { status: "ready" } : String(url).endsWith("/activities")
        ? { items: [] }
        : { items: [{ ...connector, hook_receipt: { ...connector.hook_receipt } }] },
    })))
    invoke.mockImplementation(async (command: string) => {
      if (command === "get_codex_integration") return {
        rootPath: "C:/Users/demo",
        configPath: "C:/Users/demo/.codex/config.toml",
        guidancePath: "C:/Users/demo/.codex/AGENTS.md",
        hooksPath: "C:/Users/demo/.codex/hooks.json",
        mcpInstalled: true,
        guidanceInstalled: true,
        hooksInstalled: true,
        installed: true,
        conflict: false,
      }
      if (command === "get_claude_global_integration") throw new Error("not available")
      return {}
    })
    window.__PERSONALMEMORY__ = { endpoint: "http://127.0.0.1:19191", token: "token" }

    const wrapper = mountView()
    await vi.waitFor(() => expect(wrapper.text()).toContain("记忆读取已验证 · 尚未收到 Hook 事件"))
    expect(invoke).toHaveBeenCalledWith("verify_personalmemory_mcp")
    connector.hook_receipt.assistant_message = "2026-10-09T14:01:00Z"
    await vi.advanceTimersByTimeAsync(10_000)
    await vi.waitFor(() => expect(wrapper.text()).toContain("读取与 Hook 事件均已收到"))
    expect(invoke.mock.calls.filter(([name]) => name === "verify_personalmemory_mcp")).toHaveLength(1)
    await wrapper.findAll("button").find((button) => button.text() === "移除 Codex 接入")?.trigger("click")
    expect(wrapper.find('.codex-confirm').exists()).toBe(true)
    expect(invoke).not.toHaveBeenCalledWith("remove_codex_integration")
    await wrapper.findAll("button").find((button) => button.text() === "取消")?.trigger("click")
    expect(wrapper.find('.codex-confirm').exists()).toBe(false)
    expect(wrapper.text()).toContain("记忆读取与自动记录入口均已验证")
    expect(wrapper.text()).not.toContain("待 Codex 审核")
    wrapper.unmount()
    vi.useRealTimers()
  })

  it("finishes Codex setup when recall guidance exists without an MCP connection", async () => {
    const connector = {
      id: "connector_codex",
      profile_id: "codex_global_context",
      source_path: "C:/Users/demo",
      enabled: true,
      read_enabled: true,
    }
    vi.stubGlobal("fetch", vi.fn(async (url: string) => ({
      ok: true,
      status: 200,
      json: async () => String(url).endsWith("/activities") ? { items: [] } : { items: [connector] },
    })))
    invoke.mockImplementation(async (command: string, payload?: { connectorId?: string }) => {
      if (command === "get_codex_integration") return {
        rootPath: "C:/Users/demo",
        configPath: "C:/Users/demo/.codex/config.toml",
        guidancePath: "C:/Users/demo/.codex/AGENTS.md",
        hooksPath: "C:/Users/demo/.codex/hooks.json",
        mcpInstalled: false,
        guidanceInstalled: true,
        hooksInstalled: true,
        installed: false,
        conflict: false,
      }
      if (command === "install_codex_integration") {
        expect(payload).toEqual({ connectorId: "connector_codex" })
        return {
        rootPath: "C:/Users/demo",
        configPath: "C:/Users/demo/.codex/config.toml",
        guidancePath: "C:/Users/demo/.codex/AGENTS.md",
        hooksPath: "C:/Users/demo/.codex/hooks.json",
        mcpInstalled: true,
        guidanceInstalled: true,
        hooksInstalled: true,
        installed: true,
        conflict: false,
      }
      }
      if (command === "get_claude_global_integration") throw new Error("not available")
      return {}
    })
    window.__PERSONALMEMORY__ = { endpoint: "http://127.0.0.1:19191", token: "token" }

    const wrapper = mountView()
    await vi.waitFor(() => expect(wrapper.text()).toContain("记忆读取未启用"))
    expect(wrapper.text()).toContain("自动记录已经配置")
    await wrapper.findAll("button").find((button) => button.text().includes("接入 Codex"))?.trigger("click")
    await vi.waitFor(() => expect(invoke).toHaveBeenCalledWith("install_codex_integration", { connectorId: "connector_codex" }))
  })

  it("shows a real Codex MCP name collision as a configuration conflict", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({ items: [] }) }))
    invoke.mockImplementation(async (command: string) => {
      if (command === "get_codex_integration") return {
        configPath: "C:/Users/demo/.codex/config.toml",
        guidancePath: "C:/Users/demo/.codex/AGENTS.md",
        mcpInstalled: false,
        guidanceInstalled: false,
        installed: false,
        conflict: true,
      }
      if (command === "get_claude_global_integration") throw new Error("not available")
      return {}
    })
    window.__PERSONALMEMORY__ = { endpoint: "http://127.0.0.1:19191", token: "token" }

    const wrapper = mountView()
    await vi.waitFor(() => expect(wrapper.text()).toContain("配置冲突"))
    expect(wrapper.text()).toContain("已有同名 MCP 配置")
    expect(wrapper.findAll("button").find((button) => button.text().includes("需要手动处理冲突"))?.attributes("disabled")).toBeDefined()
  })

  it.each([
    {
      name: "capture",
      settingsInstalled: false,
      mcpInstalled: true,
      state: "自动记录未启用",
      detail: "记忆读取已经连接",
      action: "启用自动记录",
      command: "enable_claude_global_capture",
    },
    {
      name: "memory read",
      settingsInstalled: true,
      mcpInstalled: false,
      state: "记忆读取未启用",
      detail: "自动记录已经启用",
      action: "启用记忆读取",
      command: "enable_claude_global_memory_read",
    },
  ])("explains and enables only the missing Claude $name capability", async ({
    settingsInstalled,
    mcpInstalled,
    state,
    detail,
    action,
    command,
  }) => {
    const connector = {
      id: "connector_global",
      name: "Claude Code 个人连续性",
      profile_id: "claude_code_global_context",
      source_path: "C:/Users/demo",
      target_path: null,
      namespace: "general",
      capabilities: { read: true, write: false },
      read_enabled: true,
      write_enabled: false,
      enabled: true,
      status: "healthy",
    }
    vi.stubGlobal("fetch", vi.fn(async (url: string) => ({
      ok: true,
      status: 200,
      json: async () => String(url).endsWith("/activities") ? { items: [] } : { items: [connector] },
    })))
    invoke.mockImplementation(async (invokedCommand: string, payload?: { connectorId?: string }) => {
      if (invokedCommand === "get_claude_global_integration") {
        return {
          rootPath: "C:/Users/demo",
          settingsPath: "C:/Users/demo/.claude/settings.json",
          mcpPath: "C:/Users/demo/.claude.json",
          settingsInstalled,
          mcpInstalled,
          mcpConflict: false,
        }
      }
      if (invokedCommand === command && command === "enable_claude_global_capture") {
        expect(payload).toEqual({ connectorId: "connector_global" })
      }
      if (invokedCommand === "get_codex_integration") throw new Error("not available")
      return {}
    })
    window.__PERSONALMEMORY__ = { endpoint: "http://127.0.0.1:19191", token: "token" }

    const wrapper = mountView()
    await vi.waitFor(() => expect(wrapper.text()).toContain(state))
    expect(wrapper.text()).toContain(detail)
    expect(wrapper.text()).not.toContain("修复接入")

    await wrapper.findAll("button").find((button) => button.text().includes(action))?.trigger("click")

    await vi.waitFor(() => expect(invoke).toHaveBeenCalledWith(
      command,
      ...(command === "enable_claude_global_capture" ? [{ connectorId: "connector_global" }] : []),
    ))
    expect(invoke).not.toHaveBeenCalledWith("install_claude_global_integration", expect.anything())
  })

  it("shows a real Claude MCP name collision as a configuration conflict", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({ items: [] }) }))
    invoke.mockImplementation(async (command: string) => {
      if (command === "get_claude_global_integration") return {
        rootPath: "C:/Users/demo",
        settingsPath: "C:/Users/demo/.claude/settings.json",
        mcpPath: "C:/Users/demo/.claude.json",
        settingsInstalled: false,
        mcpInstalled: false,
        mcpConflict: true,
      }
      if (command === "get_codex_integration") throw new Error("not available")
      return {}
    })
    window.__PERSONALMEMORY__ = { endpoint: "http://127.0.0.1:19191", token: "token" }

    const wrapper = mountView()
    await vi.waitFor(() => expect(wrapper.text()).toContain("配置冲突"))
    expect(wrapper.text()).toContain("已有同名 MCP 配置")
    expect(wrapper.findAll("button").find((button) => button.text().includes("需要手动处理冲突"))?.attributes("disabled")).toBeDefined()
  })

  it("shows connection health and the one-time authorization workflow", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({
          items: [
            {
              id: "connector_01",
              name: "本地 Agent 记忆",
              profile_id: "generic_markdown_readonly",
              source_path: "C:/Agent/MEMORY.md",
              target_path: null,
              namespace: "general",
              capabilities: { read: true, write: false },
              read_enabled: true,
              write_enabled: false,
              enabled: true,
              status: "attention",
              background_sync: true,
            },
          ],
        }),
      }),
    )
    window.__PERSONALMEMORY__ = { endpoint: "http://127.0.0.1:19191", token: "token" }

    const wrapper = mountView()
    await vi.waitFor(() => expect(wrapper.text()).toContain("本地 Agent 记忆"))

    expect(wrapper.text()).toContain("需要处理")
    expect(wrapper.text()).toContain("文件变化时自动读取")
    expect(wrapper.text()).toContain("Markdown 文件")

    await wrapper.get("button[aria-label='添加连接']").trigger("click")

    expect(wrapper.get("[role='dialog']").text()).toContain("添加连接")
    await wrapper.get("select").setValue("generic_markdown_readonly")
    expect(wrapper.get("[role='dialog']").text()).toContain("读取路径")
    expect(wrapper.get("[role='dialog']").text()).toContain("写入路径")
    expect(wrapper.get("[role='dialog']").text()).toContain("数据只在本机处理")
    expect(wrapper.get("button[data-action='authorize']").text()).toContain("授权并开始同步")

    await wrapper.get("select").setValue("continuity_manifest_file")
    expect(wrapper.get("[role='dialog']").text()).not.toContain("读取路径")
    expect(wrapper.get("[role='dialog']").text()).toContain("写入路径")
    expect(wrapper.get("[role='dialog']").text()).toContain("不覆盖 Agent 自有记忆")
    expect(wrapper.get("button[data-action='authorize']").text()).toContain("创建受管投影")
  })

  it("shows continuously polled Agent memory sources, derived-summary risk, and actual sync changes", async () => {
    const connectors = [
      {
        id: "connector_index",
        name: "Codex memory index",
        profile_id: "codex_memory_markdown",
        source_path: "C:/Users/demo/.codex/memories/MEMORY.md",
        target_path: null,
        namespace: "codex",
        capabilities: { read: true, write: false },
        read_enabled: true,
        write_enabled: false,
        enabled: true,
        status: "healthy",
        background_sync: true,
      },
      {
        id: "connector_summary",
        name: "Codex memory summary",
        profile_id: "codex_memory_markdown",
        source_path: "C:/Users/demo/.codex/memories/memory_summary.md",
        target_path: null,
        namespace: "codex",
        capabilities: { read: true, write: false },
        read_enabled: true,
        write_enabled: false,
        enabled: true,
        status: "healthy",
        background_sync: true,
      },
    ]
    const activity = {
      id: "activity_summary",
      operation_id: "operation_summary",
      connector_id: "connector_summary",
      kind: "source_ingested",
      status: "completed",
      summary: "自动处理 17 条候选记忆",
      created_at: "2026-08-05T04:17:17+00:00",
      details: {
        accepted: 0,
        provisional: 17,
        quarantined: 0,
        restricted: 0,
        discarded: 0,
        memory_changes: [
          { id: "memory_1", action: "created" },
          { id: "memory_2", action: "created" },
        ],
      },
    }
    vi.stubGlobal("fetch", vi.fn(async (url: string) => ({
      ok: true,
      status: 200,
      json: async () => String(url).endsWith("/activities") ? { items: [activity] } : { items: connectors },
    })))
    window.__PERSONALMEMORY__ = { endpoint: "http://127.0.0.1:19191", token: "token" }

    const wrapper = mountView()
    await vi.waitFor(() => expect(wrapper.text()).toContain("Codex memory summary"))

    expect(wrapper.text()).toContain("持续接入")
    expect(wrapper.text()).toContain("Agent 记忆索引")
    expect(wrapper.text()).toContain("派生摘要")
    expect(wrapper.text()).toContain("可能重复上游记忆索引")
    expect(wrapper.text()).toContain("最近处理 17 条候选 · 实际新增 2 条")
    expect(wrapper.text()).not.toContain("已导入来源")
  })

  it("installs project-level Claude integration after authorization", async () => {
    const connector = {
      id: "connector_claude",
      name: "PersonalMemory project",
      profile_id: "claude_code_hook",
      source_path: "C:/Projects/PersonalMemory",
      target_path: null,
      namespace: "projects/personalmemory",
      capabilities: { read: true, write: false },
      read_enabled: true,
      write_enabled: false,
      enabled: true,
      status: "healthy",
    }
    let listCount = 0
    vi.stubGlobal("fetch", vi.fn(async (_url: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        return { ok: true, status: 201, json: async () => connector }
      }
      listCount += 1
      return { ok: true, status: 200, json: async () => ({ items: listCount > 1 ? [connector] : [] }) }
    }))
    invoke.mockResolvedValue({})
    window.__PERSONALMEMORY__ = { endpoint: "http://127.0.0.1:19191", token: "token" }

    const wrapper = mountView()
    await vi.waitFor(() => expect(wrapper.text()).toContain("还没有持续接入"))
    await wrapper.get("button[aria-label='添加连接']").trigger("click")
    const inputs = wrapper.findAll("[role='dialog'] input")
    await inputs[0].setValue("PersonalMemory project")
    await inputs[1].setValue("C:/Projects/PersonalMemory")
    await inputs[2].setValue("projects/personalmemory")
    await wrapper.get("button[data-action='authorize']").trigger("submit")

    await vi.waitFor(() => expect(invoke).toHaveBeenCalledWith(
      "install_claude_project_integration",
      { projectRoot: "C:/Projects/PersonalMemory", connectorId: "connector_claude" },
    ))
    expect(wrapper.text()).toContain("Claude Code 项目接入已安装")
  })

  it("creates a connector once when its initial sync fails", async () => {
    const connector = {
      id: "connector_initial_sync",
      name: "Initial sync source",
      profile_id: "generic_markdown_readonly",
      source_path: "C:/Agent/MEMORY.md",
      target_path: null,
      namespace: "general",
      capabilities: { read: true, write: false },
      read_enabled: true,
      write_enabled: false,
      enabled: true,
      status: "healthy",
    }
    let createCalls = 0
    let syncCalls = 0
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === "POST" && url.endsWith("/connectors/preview")) {
        return { ok: true, status: 200, json: async () => ({}) }
      }
      if (init?.method === "POST" && url.endsWith("/connectors")) {
        createCalls += 1
        return { ok: true, status: 201, json: async () => connector }
      }
      if (init?.method === "POST" && url.endsWith("/connectors/connector_initial_sync/sync")) {
        syncCalls += 1
        return { ok: false, status: 500, json: async () => ({}) }
      }
      return { ok: true, status: 200, json: async () => ({ items: [connector] }) }
    }))
    window.__PERSONALMEMORY__ = { endpoint: "http://127.0.0.1:19191", token: "token" }

    const wrapper = mountView()
    await vi.waitFor(() => expect(wrapper.text()).toContain("Initial sync source"))
    await wrapper.get("button[aria-label='添加连接']").trigger("click")
    await wrapper.get("select").setValue("generic_markdown_readonly")
    const inputs = wrapper.findAll("[role='dialog'] input")
    await inputs[0].setValue("Initial sync source")
    await inputs[1].setValue("C:/Agent/MEMORY.md")
    await inputs[3].setValue("general")
    await wrapper.get("button[data-action='authorize']").trigger("submit")

    await vi.waitFor(() => expect(wrapper.text()).toContain("首次同步失败"))
    expect(wrapper.find("[role='dialog']").exists()).toBe(false)
    expect(createCalls).toBe(1)
    expect(syncCalls).toBe(1)
  })

  it("removes a connection while keeping imported memories intact", async () => {
    const connector = {
      id: "connector_02",
      name: "待移除连接",
      profile_id: "codex_memory_markdown",
      source_path: "C:/Users/demo/.codex/memories/MEMORY.md",
      target_path: null,
      namespace: "general",
      capabilities: { read: true, write: false },
      read_enabled: true,
      write_enabled: false,
      enabled: true,
      status: "healthy",
    }
    const getCalls = { count: 0 }
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        if (init?.method === "DELETE") {
          expect(String(url)).toContain("/connectors/connector_02")
          return { ok: true, status: 204 }
        }
        getCalls.count += 1
        return {
          ok: true,
          status: 200,
          json: async () => ({ items: getCalls.count === 1 ? [connector] : [] }),
        }
      }),
    )
    vi.spyOn(window, "confirm").mockReturnValue(true)
    window.__PERSONALMEMORY__ = { endpoint: "http://127.0.0.1:19191", token: "token" }

    const wrapper = mountView()
    await vi.waitFor(() => expect(wrapper.text()).toContain("待移除连接"))

    await wrapper.get("button[aria-label='移除连接']").trigger("click")
    await vi.waitFor(() => expect(wrapper.text()).not.toContain("待移除连接"))
    expect(window.confirm).toHaveBeenCalled()
  })

  it("keeps a row disabled and shows an error when toggling a connection fails", async () => {
    const connector = {
      id: "connector_toggle",
      name: "Toggle source",
      profile_id: "generic_markdown_readonly",
      source_path: "C:/Agent/MEMORY.md",
      target_path: null,
      namespace: "general",
      capabilities: { read: true, write: false },
      read_enabled: true,
      write_enabled: false,
      enabled: true,
      status: "healthy",
    }
    let rejectPatch: ((reason?: unknown) => void) | undefined
    vi.stubGlobal("fetch", vi.fn((_: string, init?: RequestInit) => {
      if (init?.method === "PATCH") return new Promise((_, reject) => { rejectPatch = reject })
      return Promise.resolve({ ok: true, status: 200, json: async () => ({ items: [connector] }) })
    }))
    window.__PERSONALMEMORY__ = { endpoint: "http://127.0.0.1:19191", token: "token" }

    const wrapper = mountView()
    await vi.waitFor(() => expect(wrapper.text()).toContain("Toggle source"))
    const toggle = wrapper.get("button[aria-label='暂停连接']")
    await toggle.trigger("click")

    expect(toggle.attributes("disabled")).toBeDefined()
    expect(wrapper.get("button[aria-label='立即同步']").attributes("disabled")).toBeDefined()
    rejectPatch?.(new Error("network"))
    await vi.waitFor(() => expect(wrapper.get("[role='alert']").text()).toContain("更新连接状态未完成"))
    expect(toggle.attributes("disabled")).toBeUndefined()
  })
})
