<script setup lang="ts">
import { Maximize2, RefreshCw } from "lucide-vue-next"
import { computed, onBeforeUnmount, onMounted, ref } from "vue"

import { requireClient, type MemoryGraphResponse, type MemoryItem } from "@/api/client"
import MemoryDetail from "@/components/MemoryDetail.vue"
import {
  forceLayout,
  layoutLabels,
  placeNodes,
  type GraphEdge,
  type GraphNode,
} from "@/graph/layout"
import { needsMemoryReview } from "@/memories/presentation"
import { useI18n } from "@/i18n"

const { t } = useI18n()

const TYPE_LABELS: Record<string, string> = {
  fact: "事实",
  goal: "目标",
  skill: "能力",
  preference: "偏好",
  event: "经历",
  insight: "洞察",
  decision: "决策",
}

// Neutral fills keep the canvas quiet; the outline is the semantic accent.
const TYPE_COLORS: Record<string, string> = {
  fact: "#6f9188",
  goal: "#70879c",
  skill: "#887a99",
  preference: "#a2875a",
  event: "#6d8e95",
  insight: "#a27670",
  decision: "#9b7184",
}

const TYPE_STROKES: Record<string, string> = {
  fact: "#52766c",
  goal: "#536c83",
  skill: "#6e617e",
  preference: "#80683c",
  event: "#4f727a",
  insight: "#855d58",
  decision: "#7f596b",
}

const CANVAS_BACKGROUND = "#f6f8f6"
const EDGE_COLOR = "#9da8a3"
const LABEL_COLOR = "#3f4e48"
const LABEL_HALO = "#f6f8f6"

const EDGE_STYLES: Record<
  string,
  { color: string; directed: boolean; label: string }
> = {
  supports: { color: "#5e8768", directed: true, label: "支持" },
  derived_from: { color: "#5f7f98", directed: true, label: "源自" },
  caused_by: { color: "#806d90", directed: true, label: "导致" },
  contradicts: { color: "#a36d61", directed: true, label: "矛盾" },
  related_to: { color: "#778680", directed: false, label: "相关" },
}

const loading = ref(true)
const error = ref("")
const notice = ref("")
const operationBusy = ref(false)
const payload = ref<MemoryGraphResponse | null>(null)
const nodes = ref<GraphNode[]>([])
const edges = ref<GraphEdge[]>([])
const selected = ref<MemoryItem | null>(null)
const enabledTypes = ref(new Set<string>(Object.keys(TYPE_LABELS)))
// The production graph can be derived-only. Keep the network visible on the
// first render, while drawing derived edges thinner and quieter than explicit ones.
const showDerived = ref(true)
const showNeedsReview = ref(false)

const canvas = ref<HTMLCanvasElement | null>(null)
const shell = ref<HTMLDivElement | null>(null)
let resizeObserver: ResizeObserver | null = null
let drawFrame: number | null = null

const viewport = { width: 800, height: 560 }
const transform = { offsetX: 40, offsetY: 40, zoom: 1 }
const interaction = {
  mode: "idle" as "idle" | "pan" | "node",
  nodeId: "",
  startX: 0,
  startY: 0,
  originX: 0,
  originY: 0,
  moved: false,
}
const hoverId = ref("")

const counts = computed(() => payload.value?.counts)
const activeEdgeStyles = computed(() => {
  const usedTypes = new Set(edges.value.map((edge) => edge.type))
  return Object.entries(EDGE_STYLES).filter(([type]) => usedTypes.has(type))
})
const reviewCount = computed(() =>
  nodes.value.filter((node) => needsMemoryReview(node as MemoryItem)).length,
)
const visibleNodes = computed(() =>
  nodes.value.filter((node) =>
    enabledTypes.value.has(node.type)
    && (showNeedsReview.value || !needsMemoryReview(node as MemoryItem)),
  ),
)
const visibleNodeIds = computed(() => new Set(visibleNodes.value.map((node) => node.id)))
const focusId = computed(() => {
  if (hoverId.value && visibleNodeIds.value.has(hoverId.value)) return hoverId.value
  const selectedId = selected.value?.id ?? ""
  return visibleNodeIds.value.has(selectedId) ? selectedId : ""
})
const visibleEdges = computed(() =>
  edges.value.filter(
    (edge) =>
      visibleNodeIds.value.has(edge.source) &&
      visibleNodeIds.value.has(edge.target) &&
      (!edge.derived ||
        showDerived.value ||
        (focusId.value !== "" &&
          (edge.source === focusId.value || edge.target === focusId.value))),
  ),
)

const neighbors = computed(() => {
  if (!selected.value) return []
  const id = selected.value.id
  const result: GraphNode[] = []
  const seen = new Set<string>()
  for (const edge of visibleEdges.value) {
    const otherId =
      edge.source === id ? edge.target : edge.target === id ? edge.source : ""
    if (otherId && !seen.has(otherId)) {
      seen.add(otherId)
      const node = nodes.value.find((candidate) => candidate.id === otherId)
      if (node) result.push(node)
    }
  }
  return result
})

const neighborRelations = computed(() => {
  if (!selected.value) return new Map<string, string[]>()
  const id = selected.value.id
  const relations = new Map<string, string[]>()
  for (const edge of visibleEdges.value) {
    const otherId =
      edge.source === id ? edge.target : edge.target === id ? edge.source : ""
    if (!otherId) continue
    const label = t(EDGE_STYLES[edge.type]?.label ?? edge.type)
    const list = relations.get(otherId) ?? []
    if (!list.includes(label)) list.push(label)
    relations.set(otherId, list)
  }
  return relations
})

const neighborIds = computed(() => {
  const focus = focusId.value
  const result = new Set<string>()
  if (!focus) return result
  result.add(focus)
  for (const edge of visibleEdges.value) {
    if (edge.source === focus) result.add(edge.target)
    if (edge.target === focus) result.add(edge.source)
  }
  return result
})

function selectNode(id: string) {
  selected.value = (nodes.value.find((candidate) => candidate.id === id) ??
    null) as MemoryItem | null
  requestDraw()
}

function screenX(x: number): number {
  return x * transform.zoom + transform.offsetX
}

function screenY(y: number): number {
  return y * transform.zoom + transform.offsetY
}

async function load(): Promise<boolean> {
  loading.value = true
  error.value = ""
  notice.value = ""
  const selectedId = selected.value?.id
  let succeeded = false
  try {
    payload.value = await requireClient().graph()
    edges.value = payload.value.edges
    layoutNodes(payload.value.nodes)
    selected.value = selectedId
      ? ((nodes.value.find((node) => node.id === selectedId) ?? null) as MemoryItem | null)
      : null
    succeeded = true
  } catch {
    payload.value = null
    nodes.value = []
    edges.value = []
    error.value = "暂时无法读取记忆图谱。"
  } finally {
    loading.value = false
    requestDraw()
  }
  return succeeded
}

function layoutNodes(base: MemoryGraphResponse["nodes"]) {
  const width = Math.max(viewport.width, 200)
  const height = Math.max(viewport.height, 200)
  const normalized = base.map((node) => ({
    ...node,
    clusterId: (node as unknown as { cluster_id?: string }).cluster_id ?? "",
    importance: (node as unknown as { importance?: number }).importance,
  }))
  // Clusters remain available as metadata for the detail panel, but the
  // canvas uses one continuous force field so no islands or halo cards appear.
  nodes.value = forceLayout(placeNodes(normalized, width, height, 23), edges.value, {
    width,
    height,
  })
  fitView()
}

function fitView() {
  if (!nodes.value.length) return
  const horizontalPadding = viewport.width < 520 ? 18 : 34
  const topPadding = viewport.width < 520 ? 92 : 64
  const bottomPadding = 28
  const minX = Math.min(...nodes.value.map((node) => node.x)) - 48
  const maxX = Math.max(...nodes.value.map((node) => node.x)) + 48
  const minY = Math.min(...nodes.value.map((node) => node.y)) - 48
  const maxY = Math.max(...nodes.value.map((node) => node.y)) + 48
  const spanX = Math.max(maxX - minX, 180)
  const spanY = Math.max(maxY - minY, 160)
  const availableWidth = Math.max(viewport.width - horizontalPadding * 2, 180)
  const availableHeight = Math.max(viewport.height - topPadding - bottomPadding, 180)
  transform.zoom = Math.max(
    0.3,
    Math.min(1.55, Math.min(availableWidth / spanX, availableHeight / spanY) * 0.9),
  )
  transform.offsetX = horizontalPadding + (availableWidth - spanX * transform.zoom) / 2 - minX * transform.zoom
  transform.offsetY = topPadding + (availableHeight - spanY * transform.zoom) / 2 - minY * transform.zoom
  requestDraw()
}

function toggleType(type: string) {
  const next = new Set(enabledTypes.value)
  if (next.has(type)) next.delete(type)
  else next.add(type)
  enabledTypes.value = next
  requestDraw()
}

function toggleDerived() {
  showDerived.value = !showDerived.value
  requestDraw()
}

function toggleNeedsReview() {
  showNeedsReview.value = !showNeedsReview.value
  if (
    selected.value
    && !showNeedsReview.value
    && needsMemoryReview(selected.value)
  ) {
    selected.value = null
  }
  fitView()
}

async function revise(statement: string, reason: string | null) {
  if (!selected.value || operationBusy.value) return
  operationBusy.value = true
  error.value = ""
  notice.value = ""
  try {
    selected.value = await requireClient().reviseMemory(
      selected.value.id,
      selected.value.revision,
      statement,
      reason,
    )
    if (await load()) notice.value = "记忆已更新。"
  } catch {
    error.value = "修订记忆失败，请重试。"
  } finally {
    operationBusy.value = false
  }
}

async function remove() {
  if (!selected.value || operationBusy.value) return
  if (!window.confirm(t("删除后，旧来源和旧导出包都不能恢复这条记忆。确认删除吗？"))) return
  operationBusy.value = true
  error.value = ""
  notice.value = ""
  try {
    await requireClient().deleteMemory(selected.value.id)
    selected.value = null
    if (await load()) notice.value = "记忆已删除。"
  } catch {
    error.value = "删除记忆失败，请重试。"
  } finally {
    operationBusy.value = false
  }
}

function requestDraw() {
  if (!canvas.value || drawFrame !== null) return
  drawFrame = window.requestAnimationFrame(() => {
    drawFrame = null
    draw()
  })
}

function edgeEndpoints(source: GraphNode, target: GraphNode) {
  const dx = target.x - source.x
  const dy = target.y - source.y
  const distance = Math.max(Math.hypot(dx, dy), 0.001)
  const ux = dx / distance
  const uy = dy / distance
  return {
    sx: source.x + ux * (source.radius + 1),
    sy: source.y + uy * (source.radius + 1),
    tx: target.x - ux * (target.radius + 1),
    ty: target.y - uy * (target.radius + 1),
  }
}

function draw() {
  const canvasEl = canvas.value
  if (!canvasEl) return
  const context = canvasEl.getContext("2d")
  if (!context) return
  const dpr = window.devicePixelRatio || 1
  context.setTransform(dpr, 0, 0, dpr, 0, 0)
  context.fillStyle = CANVAS_BACKGROUND
  context.fillRect(0, 0, viewport.width, viewport.height)
  const focus = focusId.value
  const highlighted = neighborIds.value
  const nodeById = new Map(nodes.value.map((node) => [node.id, node]))

  context.save()
  context.translate(transform.offsetX, transform.offsetY)
  context.scale(transform.zoom, transform.zoom)

  for (const edge of visibleEdges.value) {
    const source = nodeById.get(edge.source)
    const target = nodeById.get(edge.target)
    if (!source || !target) continue
    const active = focus === "" || edge.source === focus || edge.target === focus
    const style = EDGE_STYLES[edge.type] ?? {
      color: EDGE_COLOR,
      directed: false,
      label: edge.type,
    }
    const { sx, sy, tx, ty } = edgeEndpoints(source, target)
    context.beginPath()
    context.moveTo(sx, sy)
    context.lineTo(tx, ty)
    context.strokeStyle = style.color
    context.globalAlpha = focus === "" ? (edge.derived ? 0.2 : 0.38) : active ? 0.72 : 0.1
    context.lineWidth = active ? 1.25 : 0.8
    context.setLineDash(edge.derived ? [3, 5] : [])
    context.stroke()
    if (style.directed && (active || focus === "")) {
      const angle = Math.atan2(ty - sy, tx - sx)
      const headSize = 4.5
      context.beginPath()
      context.moveTo(tx, ty)
      context.lineTo(
        tx - headSize * Math.cos(angle - 0.38),
        ty - headSize * Math.sin(angle - 0.38),
      )
      context.lineTo(
        tx - headSize * Math.cos(angle + 0.38),
        ty - headSize * Math.sin(angle + 0.38),
      )
      context.closePath()
      context.fillStyle = style.color
      context.globalAlpha = focus === "" ? 0.4 : active ? 0.84 : 0.1
      context.fill()
    }
  }
  context.setLineDash([])

  for (const node of visibleNodes.value) {
    const isFocus = focus !== "" && highlighted.has(node.id)
    const isCenter = node.id === focus
    context.save()
    context.globalAlpha = focus === "" ? 0.92 : isFocus ? 1 : 0.2
    context.beginPath()
    context.arc(node.x, node.y, node.radius, 0, Math.PI * 2)
    context.fillStyle = TYPE_COLORS[node.type] ?? "#788680"
    context.fill()
    context.strokeStyle = isCenter ? "#284b3e" : TYPE_STROKES[node.type] ?? "#60716a"
    context.lineWidth = isCenter ? 2.4 : isFocus ? 1.8 : 1
    context.stroke()
    context.restore()
  }
  context.restore()

  // Labels are screen-space text so they stay readable at the initial fit and
  // while zooming. Every visible node receives one; focus only changes alpha.
  const labels = layoutLabels(visibleNodes.value, {
    width: viewport.width,
    height: viewport.height,
    zoom: transform.zoom,
    offsetX: transform.offsetX,
    offsetY: transform.offsetY,
    fontSize: viewport.width < 520 ? 9 : visibleNodes.value.length > 64 ? 10 : 11,
    maxChars: viewport.width < 520 ? 8 : 10,
    gap: visibleNodes.value.length > 64 ? 3 : 7,
    reserved: [{ x: 0, y: 0, width: viewport.width, height: viewport.width < 520 ? 82 : 54 }],
  })
  context.save()
  const labelFontSize = viewport.width < 520 ? 9 : visibleNodes.value.length > 64 ? 10 : 11
  context.font = `500 ${labelFontSize}px 'Segoe UI', 'Microsoft YaHei UI', sans-serif`
  context.textBaseline = "middle"
  for (const label of labels) {
    const isFocus = focus === "" || highlighted.has(label.id)
    const isCenter = label.id === focus
    const textX = label.align === "left"
      ? label.x + 4
      : label.align === "right"
        ? label.x + label.width - 4
        : label.x + label.width / 2
    const textY = label.y + label.height / 2
    context.globalAlpha = focus === "" ? 0.86 : isFocus ? 1 : 0.45
    context.textAlign = label.align
    context.lineWidth = isCenter ? 3.5 : 3
    context.strokeStyle = LABEL_HALO
    context.strokeText(label.text, textX, textY)
    context.fillStyle = isCenter ? "#263f35" : LABEL_COLOR
    context.font = `${isCenter ? 650 : 500} ${labelFontSize}px 'Segoe UI', 'Microsoft YaHei UI', sans-serif`
    context.fillText(label.text, textX, textY)
  }
  context.restore()
}

function resizeCanvas() {
  const shellEl = shell.value
  const canvasEl = canvas.value
  if (!shellEl || !canvasEl) return
  viewport.width = Math.max(shellEl.clientWidth, 200)
  viewport.height = Math.max(shellEl.clientHeight, 280)
  const dpr = window.devicePixelRatio || 1
  canvasEl.width = Math.round(viewport.width * dpr)
  canvasEl.height = Math.round(viewport.height * dpr)
  canvasEl.style.width = `${viewport.width}px`
  canvasEl.style.height = `${viewport.height}px`
  if (nodes.value.length) fitView()
  else requestDraw()
}

function hitTest(clientX: number, clientY: number): string {
  const rect = canvas.value?.getBoundingClientRect()
  if (!rect) return ""
  const mx = clientX - rect.left
  const my = clientY - rect.top
  for (let index = visibleNodes.value.length - 1; index >= 0; index -= 1) {
    const node = visibleNodes.value[index]
    const dx = mx - screenX(node.x)
    const dy = my - screenY(node.y)
    const hitRadius = Math.max(node.radius * transform.zoom + 5, 10)
    if (Math.hypot(dx, dy) <= hitRadius) return node.id
  }
  return ""
}

function onPointerDown(event: PointerEvent) {
  const canvasEl = canvas.value
  if (!canvasEl) return
  try {
    canvasEl.setPointerCapture(event.pointerId)
  } catch {
    // Pointer capture is unavailable in a few embedded webviews.
  }
  const hit = hitTest(event.clientX, event.clientY)
  interaction.mode = hit ? "node" : "pan"
  interaction.nodeId = hit
  interaction.startX = event.clientX
  interaction.startY = event.clientY
  interaction.originX = transform.offsetX
  interaction.originY = transform.offsetY
  interaction.moved = false
}

function onPointerMove(event: PointerEvent) {
  if (interaction.mode === "idle") {
    const hit = hitTest(event.clientX, event.clientY)
    if (hit !== hoverId.value) {
      hoverId.value = hit
      requestDraw()
    }
    return
  }
  const dx = event.clientX - interaction.startX
  const dy = event.clientY - interaction.startY
  if (Math.hypot(dx, dy) > 4) interaction.moved = true
  if (interaction.mode === "pan") {
    transform.offsetX = interaction.originX + dx
    transform.offsetY = interaction.originY + dy
    requestDraw()
  } else if (interaction.mode === "node") {
    const node = nodes.value.find((candidate) => candidate.id === interaction.nodeId)
    if (node) {
      node.x += dx / transform.zoom
      node.y += dy / transform.zoom
      interaction.startX = event.clientX
      interaction.startY = event.clientY
      requestDraw()
    }
  }
}

function onPointerUp(event: PointerEvent) {
  const wasNode = interaction.mode === "node"
  const nodeId = interaction.nodeId
  const wasMoved = interaction.moved
  interaction.mode = "idle"
  try {
    canvas.value?.releasePointerCapture(event.pointerId)
  } catch {
    // The pointer may already have been released by the host window.
  }
  if (!wasMoved) {
    if (wasNode && nodeId) selectNode(nodeId)
    else if (!nodeId) selected.value = null
  }
  interaction.nodeId = ""
  requestDraw()
}

function onPointerLeave() {
  if (interaction.mode !== "idle" || !hoverId.value) return
  hoverId.value = ""
  requestDraw()
}

function onWheel(event: WheelEvent) {
  event.preventDefault()
  const rect = canvas.value?.getBoundingClientRect()
  if (!rect) return
  const factor = Math.exp(-event.deltaY * 0.0012)
  const nextZoom = Math.max(0.2, Math.min(3.5, transform.zoom * factor))
  const mx = event.clientX - rect.left
  const my = event.clientY - rect.top
  const worldX = (mx - transform.offsetX) / transform.zoom
  const worldY = (my - transform.offsetY) / transform.zoom
  transform.zoom = nextZoom
  transform.offsetX = mx - worldX * nextZoom
  transform.offsetY = my - worldY * nextZoom
  requestDraw()
}

onMounted(() => {
  if (typeof ResizeObserver !== "undefined") {
    resizeObserver = new ResizeObserver(resizeCanvas)
    if (shell.value) resizeObserver.observe(shell.value)
  }
  resizeCanvas()
  void load()
})

onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  if (drawFrame !== null) window.cancelAnimationFrame(drawFrame)
})
</script>

<template>
  <section class="page graph-page">
    <header class="page-header">
      <div>
        <h1 class="page-title">{{ t("记忆图谱") }}</h1>
        <p class="page-subtitle">
          {{ t("在同一张画布上浏览记忆之间的关系；悬停或选中节点可聚焦一跳关联。") }}
        </p>
      </div>
      <div class="header-actions">
        <button
          class="command-button"
          type="button"
          :aria-label="t('复位视图')"
          :disabled="loading || operationBusy"
          @click="fitView"
        >
          <Maximize2 :size="16" />
          {{ t("复位视图") }}
        </button>
        <button
          class="command-button"
          type="button"
          :aria-label="t('刷新图谱')"
          :disabled="loading || operationBusy"
          @click="load"
        >
          <RefreshCw :size="16" :class="{ spin: loading }" />
          {{ t("刷新") }}
        </button>
      </div>
    </header>

    <p v-if="error" class="inline-error" role="alert">{{ error }}</p>
    <p v-if="notice" class="inline-notice" role="status">{{ notice }}</p>

    <div class="graph-shell" ref="shell">
      <canvas
        ref="canvas"
        :aria-label="t('记忆关系图谱')"
        @pointerdown="onPointerDown"
        @pointermove="onPointerMove"
        @pointerup="onPointerUp"
        @pointercancel="onPointerUp"
        @pointerleave="onPointerLeave"
        @wheel.prevent="onWheel"
      />

      <div class="graph-toolbar" role="group" :aria-label="t('图谱筛选')">
        <span class="toolbar-label">{{ t("筛选") }}</span>
        <div class="type-filters">
          <button
            v-for="(label, type) in TYPE_LABELS"
            :key="type"
            class="type-filter"
            :class="{ off: !enabledTypes.has(type) }"
            type="button"
            :aria-pressed="enabledTypes.has(type)"
            :title="`${enabledTypes.has(type) ? t('隐藏') : t('显示')}${t(label)}`"
            @click="toggleType(type)"
          >
            <span class="type-dot" :style="{ background: TYPE_COLORS[type] }" />
            {{ t(label) }}
          </button>
        </div>
        <label class="derived-toggle">
          <input :checked="showDerived" type="checkbox" @change="toggleDerived" />
          {{ t("推导边") }}
        </label>
        <label v-if="reviewCount" class="derived-toggle review-toggle">
          <input :checked="showNeedsReview" type="checkbox" @change="toggleNeedsReview" />
          {{ t("显示需检查（{count}）", { count: reviewCount }) }}
        </label>
        <div v-if="activeEdgeStyles.length" class="edge-key" :aria-label="t('关系类型')">
          <span v-for="[type, style] in activeEdgeStyles" :key="type" class="edge-key-item">
            <span class="edge-swatch" :style="{ background: style.color }" />
            {{ t(style.label) }}
          </span>
        </div>
        <span v-if="counts" class="graph-counts">
          {{ visibleNodes.length }} / {{ nodes.length }} {{ t("节点") }} · {{ visibleEdges.length }} {{ t("边") }}
        </span>
      </div>

      <div v-if="loading" class="canvas-overlay">{{ t("正在加载记忆图谱...") }}</div>
      <div v-else-if="!visibleNodes.length" class="canvas-overlay">
        {{ nodes.length ? t("当前筛选没有可见节点。") : t("还没有记忆，先去“识别”或“连接”页导入来源。") }}
      </div>

      <div v-if="selected" class="graph-popover">
        <div class="neighbors">
          <div class="neighbors-title">{{ t("关联记忆（{count}）", { count: neighbors.length }) }}</div>
          <button
            v-for="node in neighbors"
            :key="node.id"
            class="neighbor-row"
            type="button"
            @click="selectNode(node.id)"
          >
            <span class="neighbor-dot" :style="{ background: TYPE_COLORS[node.type] }" />
            <span class="neighbor-type">{{ t(TYPE_LABELS[node.type] || node.type) }}</span>
            <span class="neighbor-text">{{ node.statement }}</span>
            <span class="neighbor-relations">
              {{ (neighborRelations.get(node.id) ?? []).join("·") }}
            </span>
          </button>
          <div v-if="!neighbors.length" class="neighbors-empty">{{ t("这条记忆暂无关联。") }}</div>
        </div>
        <MemoryDetail
          :memory="selected"
          :busy="operationBusy"
          @close="selected = null"
          @revise="revise"
          @delete="remove"
        />
      </div>
    </div>
  </section>
</template>

<style scoped>
.graph-page header {
  margin-bottom: 14px;
}

.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 18px;
}

.header-actions {
  display: flex;
  flex: none;
  gap: 8px;
}

.inline-error,
.inline-notice {
  margin: 0 0 12px;
  padding: 9px 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
}

.inline-error {
  border-color: #efc9c9;
  background: var(--danger-soft);
  color: var(--danger);
}

.inline-notice {
  border-color: #b9d9c0;
  background: #eff9f1;
  color: #216e39;
}

.graph-shell {
  position: relative;
  height: clamp(440px, calc(100vh - 240px), 720px);
  min-height: 440px;
  overflow: hidden;
  border: 1px solid var(--border);
  border-radius: 7px;
  background: #f6f8f6;
}

canvas {
  display: block;
  width: 100%;
  height: 100%;
  touch-action: none;
  cursor: grab;
}

canvas:active {
  cursor: grabbing;
}

.graph-toolbar {
  position: absolute;
  z-index: 2;
  top: 10px;
  right: 10px;
  left: 10px;
  display: flex;
  min-height: 38px;
  align-items: center;
  gap: 7px;
  padding: 6px 8px;
  border: 1px solid rgb(63 78 72 / 14%);
  border-radius: 6px;
  background: rgb(248 250 248 / 94%);
  box-shadow: 0 3px 12px rgb(35 52 44 / 7%);
  color: var(--text-muted);
  font-size: 11px;
}

.toolbar-label {
  flex: none;
  color: var(--text);
  font-weight: 700;
}

.type-filters {
  display: flex;
  min-width: 0;
  flex: 1 1 auto;
  flex-wrap: wrap;
  gap: 3px;
}

.type-filter {
  display: inline-flex;
  min-height: 24px;
  align-items: center;
  gap: 4px;
  padding: 2px 6px;
  border: 1px solid transparent;
  border-radius: 4px;
  background: transparent;
  color: var(--text);
  font-size: 11px;
}

.type-filter:hover,
.type-filter:focus-visible {
  border-color: var(--border);
  background: rgb(255 255 255 / 65%);
}

.type-filter.off {
  opacity: 0.36;
  text-decoration: line-through;
}

.type-dot,
.neighbor-dot {
  display: inline-block;
  width: 7px;
  height: 7px;
  flex: none;
  border-radius: 50%;
}

.derived-toggle {
  display: inline-flex;
  flex: none;
  align-items: center;
  gap: 4px;
  white-space: nowrap;
}

.derived-toggle input {
  margin: 0;
  accent-color: var(--brand);
}

.edge-key {
  display: flex;
  flex: none;
  flex-wrap: wrap;
  gap: 5px;
  padding-left: 5px;
  border-left: 1px solid var(--border);
}

.edge-key-item {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  white-space: nowrap;
}

.edge-swatch {
  width: 11px;
  height: 2px;
}

.graph-counts {
  flex: none;
  padding-left: 5px;
  border-left: 1px solid var(--border);
  white-space: nowrap;
}

.canvas-overlay {
  position: absolute;
  z-index: 1;
  inset: 0;
  display: grid;
  place-items: center;
  padding: 96px 22px 22px;
  color: var(--text-muted);
  pointer-events: none;
  font-size: 14px;
  text-align: center;
}

.graph-popover {
  position: absolute;
  z-index: 3;
  right: 14px;
  bottom: 14px;
  display: flex;
  width: min(360px, calc(100% - 28px));
  max-height: min(520px, calc(100% - 28px));
  flex-direction: column;
  overflow: hidden;
  overscroll-behavior: contain;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface);
  box-shadow: 0 12px 34px rgb(25 39 32 / 18%);
}

.graph-popover :deep(.detail) {
  position: static;
  display: flex;
  min-width: 0;
  min-height: 0;
  flex: 1 1 auto;
  flex-direction: column;
  overflow: hidden;
  border-left: 0;
}

.graph-popover :deep(.detail > header) {
  min-width: 0;
  flex: none;
  gap: 8px;
  padding: 12px 14px;
}

.graph-popover :deep(.detail > header > :first-child) {
  min-width: 0;
}

.graph-popover :deep(.detail h2) {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.graph-popover :deep(.detail .header-actions) {
  min-width: 0;
  max-width: 100%;
  flex: 0 1 auto;
  gap: 5px;
}

.graph-popover :deep(.detail .header-actions .command-button) {
  min-width: 0;
  padding-inline: 8px;
  white-space: nowrap;
}

.graph-popover :deep(.detail-body) {
  min-width: 0;
  min-height: 0;
  overflow-x: hidden;
  overflow-y: auto;
  gap: 14px;
  padding: 14px;
}

.graph-popover :deep(.detail-body label),
.graph-popover :deep(.detail-body p),
.graph-popover :deep(.detail-body dt),
.graph-popover :deep(.detail-body dd) {
  min-width: 0;
  max-width: 100%;
  overflow-wrap: anywhere;
  word-break: break-word;
  white-space: normal;
}

.graph-popover :deep(.detail-body textarea) {
  min-width: 0;
  max-width: 100%;
}

.graph-popover :deep(.detail-body dl div) {
  min-width: 0;
  align-items: flex-start;
  gap: 10px;
}

.graph-popover :deep(.detail-body dd) {
  flex: 0 1 64%;
  text-align: right;
}

.neighbors {
  min-width: 0;
  min-height: 0;
  max-height: 190px;
  flex: 0 1 190px;
  overflow-x: hidden;
  overflow-y: auto;
  padding: 11px 14px;
  border-bottom: 1px solid var(--border);
}

.neighbors-title {
  margin-bottom: 7px;
  color: var(--text-muted);
  font-size: 12px;
  font-weight: 600;
}

.neighbor-row {
  display: grid;
  width: 100%;
  min-width: 0;
  grid-template-columns: 8px 36px minmax(0, 1fr) minmax(0, 62px);
  align-items: center;
  gap: 7px;
  padding: 7px 6px;
  border: 0;
  border-radius: var(--radius);
  background: transparent;
  text-align: left;
}

.neighbor-row:hover {
  background: var(--surface-subtle);
}

.neighbor-type,
.neighbor-relations {
  color: var(--text-muted);
  font-size: 11px;
}

.neighbor-text {
  min-width: 0;
  overflow: hidden;
  color: var(--text);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.neighbor-relations {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.neighbors-empty {
  padding: 6px 0;
  color: var(--text-muted);
  font-size: 12px;
}

.spin {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

@media (max-width: 760px) {
  .page-header {
    flex-direction: column;
    gap: 10px;
  }

  .header-actions {
    width: 100%;
  }

  .graph-shell {
    height: clamp(440px, calc(100vh - 268px), 680px);
  }

  .graph-toolbar {
    align-items: flex-start;
    flex-wrap: wrap;
    gap: 4px 6px;
    padding: 6px;
  }

  .toolbar-label {
    width: 100%;
  }

  .type-filters {
    flex-basis: 100%;
  }

  .edge-key {
    display: none;
  }

  .graph-counts {
    margin-left: auto;
  }

  .graph-popover {
    right: 8px;
    bottom: 8px;
    width: calc(100% - 16px);
    max-height: calc(100% - 16px);
  }
}

@media (max-width: 420px) {
  .graph-shell {
    min-height: 420px;
  }

  .type-filter {
    padding-inline: 4px;
  }

  .graph-counts {
    width: 100%;
    padding-left: 0;
    border-left: 0;
  }
}
</style>
