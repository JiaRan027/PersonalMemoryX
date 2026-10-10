/// <reference types="vite/client" />

interface Window {
  __PERSONALMEMORY__?: {
    endpoint: string
    token: string
    mcpCommand?: string
    mcpJson?: string
  }
}
