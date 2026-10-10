import { defineStore } from "pinia"

import { runtimeClient } from "@/api/client"

export type ServiceState = "connecting" | "ready" | "degraded" | "protected" | "disconnected"

export const useAppStore = defineStore("app", {
  state: () => ({
    serviceState: (runtimeClient() ? "connecting" : "disconnected") as ServiceState,
  }),
  actions: {
    async connect() {
      const client = runtimeClient()
      if (!client) {
        this.serviceState = "disconnected"
        return
      }
      this.serviceState = "connecting"
      try {
        const health = await client.health()
        this.serviceState = health.status
      } catch {
        this.serviceState = "disconnected"
      }
    },
  },
})
