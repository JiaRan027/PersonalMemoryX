import { createPinia } from "pinia"
import { createApp } from "vue"

import App from "@/App.vue"
import { initializeLocale } from "@/i18n"
import { router } from "@/router"
import "@/styles/tokens.css"
import "@/styles/base.css"

if (
  import.meta.env.DEV &&
  import.meta.env.VITE_PERSONALMEMORY_ENDPOINT &&
  import.meta.env.VITE_PERSONALMEMORY_TOKEN
) {
  window.__PERSONALMEMORY__ = {
    endpoint: import.meta.env.VITE_PERSONALMEMORY_ENDPOINT,
    token: import.meta.env.VITE_PERSONALMEMORY_TOKEN,
  }
}

await initializeLocale()
createApp(App).use(createPinia()).use(router).mount("#app")
