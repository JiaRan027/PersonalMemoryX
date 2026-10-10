import { mount } from "@vue/test-utils"
import { afterEach, describe, expect, it, vi } from "vitest"

import SettingsView from "@/views/SettingsView.vue"

const mocks = vi.hoisted(() => ({
  disable: vi.fn(),
  enable: vi.fn(),
  invoke: vi.fn(),
  isEnabled: vi.fn(),
}))

vi.mock("@tauri-apps/api/core", () => ({ invoke: mocks.invoke }))
vi.mock("@tauri-apps/plugin-autostart", () => ({
  disable: mocks.disable,
  enable: mocks.enable,
  isEnabled: mocks.isEnabled,
}))

afterEach(() => {
  Object.values(mocks).forEach((mock) => mock.mockReset())
})

function preferences(showWindow = false, locale = "zh-CN") {
  return { show_window_on_autostart: showWindow, locale }
}

describe("SettingsView", () => {
  it("shows the real Windows autostart state", async () => {
    mocks.isEnabled.mockResolvedValue(true)
    mocks.invoke.mockResolvedValue(preferences(false))

    const wrapper = mount(SettingsView)

    await vi.waitFor(() => expect(wrapper.text()).toContain("已开启"))
    expect(wrapper.get<HTMLInputElement>("[data-autostart-toggle]").element.checked).toBe(true)
    expect(wrapper.get<HTMLInputElement>("[data-startup-window-toggle]").element.checked).toBe(false)
    expect(wrapper.text()).toContain("后台运行")
  })

  it("enables autostart and verifies the system state", async () => {
    mocks.isEnabled.mockResolvedValueOnce(false).mockResolvedValueOnce(true)
    mocks.enable.mockResolvedValue(undefined)
    mocks.invoke.mockResolvedValue(preferences(false))
    const wrapper = mount(SettingsView)
    await vi.waitFor(() => expect(wrapper.text()).toContain("已关闭"))

    await wrapper.get("[data-autostart-toggle]").setValue(true)

    await vi.waitFor(() => expect(wrapper.text()).toContain("下次登录 Windows"))
    expect(mocks.enable).toHaveBeenCalledOnce()
    expect(mocks.isEnabled).toHaveBeenCalledTimes(2)
  })

  it("restores the visible state when Windows rejects the change", async () => {
    mocks.isEnabled.mockResolvedValue(false)
    mocks.enable.mockRejectedValue(new Error("registry denied"))
    mocks.invoke.mockResolvedValue(preferences(false))
    const wrapper = mount(SettingsView)
    await vi.waitFor(() => expect(wrapper.text()).toContain("已关闭"))

    await wrapper.get("[data-autostart-toggle]").setValue(true)

    await vi.waitFor(() => expect(wrapper.get("[role='alert']").text()).toContain("没有生效"))
    expect(wrapper.get<HTMLInputElement>("[data-autostart-toggle]").element.checked).toBe(false)
  })

  it("persists whether an autostart should show the main window", async () => {
    mocks.isEnabled.mockResolvedValue(true)
    mocks.invoke
      .mockResolvedValueOnce(preferences(false))
      .mockResolvedValueOnce(preferences(true))
    const wrapper = mount(SettingsView)
    await vi.waitFor(() => expect(wrapper.text()).toContain("已开启"))

    await wrapper.get("[data-startup-window-toggle]").setValue(true)

    await vi.waitFor(() => expect(wrapper.text()).toContain("会显示主窗口"))
    expect(mocks.invoke).toHaveBeenLastCalledWith("set_autostart_show_window", {
      showWindow: true,
    })
  })

  it("switches and persists the interface language", async () => {
    mocks.isEnabled.mockResolvedValue(false)
    mocks.invoke
      .mockResolvedValueOnce(preferences(false, "zh-CN"))
      .mockResolvedValueOnce(preferences(false, "en-US"))
    const wrapper = mount(SettingsView)
    await vi.waitFor(() => expect(wrapper.get("[data-locale-select]").attributes("disabled")).toBeUndefined())

    await wrapper.get("[data-locale-select]").setValue("en-US")

    await vi.waitFor(() => expect(mocks.invoke).toHaveBeenLastCalledWith("set_desktop_locale", { locale: "en-US" }))
    await vi.waitFor(() => expect(wrapper.text()).toContain("Language & display"))
    expect(wrapper.text()).toContain("View third-party notices")
    expect(mocks.invoke).toHaveBeenLastCalledWith("set_desktop_locale", { locale: "en-US" })
    expect((wrapper.get("[data-locale-select]").element as HTMLSelectElement).value).toBe("en-US")
  })

  it("shows AGPL legal notices and can display the bundled license text", async () => {
    mocks.isEnabled.mockResolvedValue(false)
    mocks.invoke.mockImplementation((command: string) => {
      if (command === "get_license_text") return Promise.resolve("GNU AFFERO GENERAL PUBLIC LICENSE")
      return Promise.resolve(preferences(false))
    })
    const wrapper = mount(SettingsView)

    await vi.waitFor(() => expect(wrapper.text()).toContain("Legal notices"))
    expect(wrapper.text()).toContain("Copyright © 2026 JiaRan027")
    expect(wrapper.text()).toContain("AGPL-3.0-or-later")
    expect(wrapper.text()).toContain("https://github.com/JiaRan027/PersonalMemory")

    await wrapper.get("[data-license-toggle]").trigger("click")

    await vi.waitFor(() =>
      expect(wrapper.get("[data-license-text]").text()).toContain("GNU AFFERO GENERAL PUBLIC LICENSE"),
    )
    expect(mocks.invoke).toHaveBeenCalledWith("get_license_text")
  })

  it("loads and displays the bundled third-party notices", async () => {
    mocks.isEnabled.mockResolvedValue(false)
    mocks.invoke.mockImplementation((command: string) => {
      if (command === "get_third_party_notices_text") {
        return Promise.resolve("THIRD-PARTY NOTICES\nExample dependency license")
      }
      return Promise.resolve(preferences(false))
    })
    const wrapper = mount(SettingsView)

    await vi.waitFor(() => expect(wrapper.text()).toContain("第三方依赖及其许可文本随安装包提供。"))
    await wrapper.get("[data-third-party-toggle]").trigger("click")

    await vi.waitFor(() =>
      expect(wrapper.get("[data-third-party-notices]").text()).toContain("Example dependency license"),
    )
    expect(mocks.invoke).toHaveBeenCalledWith("get_third_party_notices_text")
  })
})
