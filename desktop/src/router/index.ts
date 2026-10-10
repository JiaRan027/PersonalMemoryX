import { createRouter, createWebHashHistory } from "vue-router"

import ActivityView from "@/views/ActivityView.vue"
import ConnectionsView from "@/views/ConnectionsView.vue"
import DataSecurityView from "@/views/DataSecurityView.vue"
import ExceptionsView from "@/views/ExceptionsView.vue"
import GraphView from "@/views/GraphView.vue"
import RecognitionView from "@/views/RecognitionView.vue"
import MemoriesView from "@/views/MemoriesView.vue"
import OverviewView from "@/views/OverviewView.vue"
import SettingsView from "@/views/SettingsView.vue"

export const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: "/", name: "overview", component: OverviewView },
    { path: "/connections", name: "connections", component: ConnectionsView },
    { path: "/memories", name: "memories", component: MemoriesView },
    { path: "/activity", name: "activity", component: ActivityView },
    { path: "/exceptions", name: "exceptions", component: ExceptionsView },
    { path: "/recognition", name: "recognition", component: RecognitionView },
    { path: "/graph", name: "graph", component: GraphView },
    { path: "/data-security", name: "data-security", component: DataSecurityView },
    { path: "/settings", name: "settings", component: SettingsView },
  ],
})
