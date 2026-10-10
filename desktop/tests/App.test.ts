import { mount } from "@vue/test-utils"
import { createPinia } from "pinia"
import { describe, expect, it } from "vitest"

import App from "@/App.vue"
import { router } from "@/router"

describe("PersonalMemory application shell", () => {
  it("shows every primary workspace and a recoverable disconnected state", async () => {
    const wrapper = mount(App, {
      global: {
        plugins: [createPinia(), router],
      },
    })
    await router.isReady()

    for (const label of ["首页", "记忆", "图谱", "接入", "活动", "迁移", "设置"]) {
      expect(wrapper.text()).toContain(label)
    }
    expect(wrapper.text()).not.toContain("识别")
    expect(wrapper.text()).not.toContain("异常")
    expect(wrapper.text()).toContain("服务未连接")
    expect(wrapper.find("button[aria-label='重新连接']").exists()).toBe(true)
  })
})
