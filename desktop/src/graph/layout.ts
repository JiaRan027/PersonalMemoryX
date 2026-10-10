export interface GraphNodeBase {
  id: string
  type: string
  statement: string
  reason: string | null
  namespace: string
  sensitivity: string
  epistemic_state: string
  status: string
  revision: number
  payload: Record<string, unknown>
  created_at: string
  updated_at: string
}

export interface GraphCluster {
  id: string
  label: string
  size: number
  node_ids: string[]
}

export interface GraphNode extends GraphNodeBase {
  x: number
  y: number
  vx: number
  vy: number
  radius: number
  clusterId?: string
  importance?: number
}

export interface GraphEdge {
  source: string
  target: string
  type: string
  origin: string
  derived: boolean
  score?: number
}

export interface GraphLayoutOptions {
  width: number
  height: number
  iterations?: number
  seed?: number
  clusterCenters?: Map<string, { x: number; y: number }>
  clusterStrength?: number
}

export interface LabelLayoutNode {
  id: string
  x: number
  y: number
  radius: number
  statement: string
  importance?: number
}

export interface LabelRect {
  x: number
  y: number
  width: number
  height: number
}

export interface LabelPlacement extends LabelRect {
  id: string
  text: string
  align: "left" | "center" | "right"
}

export interface LabelLayoutOptions {
  width: number
  height: number
  zoom?: number
  offsetX?: number
  offsetY?: number
  fontSize?: number
  maxChars?: number
  gap?: number
  reserved?: LabelRect[]
}

const FORCES = {
  repulsion: 9000,
  springLength: 150,
  springStrength: 0.012,
  gravity: 0.0015,
  damping: 0.82,
  velocityCap: 32,
  margin: 48,
  collisionPadding: 16,
}

// Story-chain layers: goals on top, then skills, events, then insights and
// decisions at the bottom. Facts/preferences sit mid-band as context.
const TYPE_LAYER: Record<string, number> = {
  goal: 0,
  skill: 1,
  event: 2,
  fact: 2.6,
  preference: 2.6,
  insight: 3,
  decision: 4,
}

function textWidth(text: string, fontSize: number): number {
  let width = 0
  for (const character of text) {
    // East Asian glyphs occupy a full em; Latin text is narrower in the UI font.
    width += /[\u2e80-\u9fff\uf900-\ufaff]/u.test(character)
      ? fontSize
      : fontSize * 0.56
  }
  return width
}

export function shortLabel(statement: string, maxChars = 26): string {
  const normalized = statement.replace(/\s+/gu, " ").trim()
  if (normalized.length <= maxChars) return normalized
  if (maxChars <= 1) return normalized.slice(0, maxChars)
  return `${normalized.slice(0, maxChars - 1)}\u2026`
}

function intersectionArea(a: LabelRect, b: LabelRect): number {
  const width = Math.max(0, Math.min(a.x + a.width, b.x + b.width) - Math.max(a.x, b.x))
  const height = Math.max(0, Math.min(a.y + a.height, b.y + b.height) - Math.max(a.y, b.y))
  return width * height
}

function circleIntersectionScore(rect: LabelRect, node: LabelLayoutNode, zoom: number, offsetX: number, offsetY: number): number {
  const centerX = node.x * zoom + offsetX
  const centerY = node.y * zoom + offsetY
  const closestX = Math.max(rect.x, Math.min(centerX, rect.x + rect.width))
  const closestY = Math.max(rect.y, Math.min(centerY, rect.y + rect.height))
  const distance = Math.hypot(centerX - closestX, centerY - closestY)
  const radius = node.radius * zoom + 4
  return distance < radius ? (radius - distance) * (radius - distance) : 0
}

/**
 * Places every node label in screen coordinates. Candidate sides are scored
 * against already placed labels, node circles, the viewport, and reserved UI
 * rectangles so the initial fit remains readable without hiding labels.
 */
export function layoutLabels(
  nodes: LabelLayoutNode[],
  options: LabelLayoutOptions,
): LabelPlacement[] {
  if (!nodes.length) return []
  const width = Math.max(options.width, 1)
  const height = Math.max(options.height, 1)
  const zoom = Math.max(options.zoom ?? 1, 0.05)
  const offsetX = options.offsetX ?? 0
  const offsetY = options.offsetY ?? 0
  const fontSize = options.fontSize ?? 11
  const gap = options.gap ?? 7
  const density = nodes.length / (width * height)
  const densityLimit = nodes.length > 64
    ? 10
    : nodes.length > 42
      ? 13
      : density > 0.0007
        ? 16
        : density > 0.00035
          ? 20
          : 26
  const maxChars = Math.max(8, Math.min(options.maxChars ?? densityLimit, densityLimit))
  const reserved = options.reserved ?? []
  const placed: LabelPlacement[] = []
  const ordered = [...nodes].sort((a, b) => {
    const importanceDelta = (b.importance ?? 0.5) - (a.importance ?? 0.5)
    return importanceDelta || a.id.localeCompare(b.id)
  })

  for (const node of ordered) {
    const text = shortLabel(node.statement, maxChars)
    const labelWidth = Math.max(24, textWidth(text, fontSize) + 8)
    const labelHeight = fontSize + 8
    const centerX = node.x * zoom + offsetX
    const centerY = node.y * zoom + offsetY
    const radius = node.radius * zoom
    const candidates: Array<{ rect: LabelRect; align: LabelPlacement["align"] }> = [
      { rect: { x: centerX - labelWidth / 2, y: centerY - radius - gap - labelHeight, width: labelWidth, height: labelHeight }, align: "center" },
      { rect: { x: centerX - labelWidth / 2, y: centerY + radius + gap, width: labelWidth, height: labelHeight }, align: "center" },
      { rect: { x: centerX + radius + gap, y: centerY - labelHeight / 2, width: labelWidth, height: labelHeight }, align: "left" },
      { rect: { x: centerX - radius - gap - labelWidth, y: centerY - labelHeight / 2, width: labelWidth, height: labelHeight }, align: "right" },
      { rect: { x: centerX + radius * 0.7 + gap, y: centerY - radius - gap - labelHeight, width: labelWidth, height: labelHeight }, align: "left" },
      { rect: { x: centerX - radius * 0.7 - gap - labelWidth, y: centerY + radius + gap, width: labelWidth, height: labelHeight }, align: "right" },
    ]
    let best = candidates[0]
    let bestScore = Number.POSITIVE_INFINITY
    candidates.forEach((candidate, candidateIndex) => {
      let score = candidateIndex * 0.02
      const rect = candidate.rect
      const overflow = Math.max(0, -rect.x) + Math.max(0, -rect.y)
        + Math.max(0, rect.x + rect.width - width) + Math.max(0, rect.y + rect.height - height)
      score += overflow * 20
      for (const other of placed) score += intersectionArea(rect, other) * 5
      for (const other of nodes) {
        if (other.id === node.id) continue
        score += circleIntersectionScore(rect, other, zoom, offsetX, offsetY) * 4
      }
      for (const other of reserved) score += intersectionArea(rect, other) * 8
      if (score < bestScore) {
        best = candidate
        bestScore = score
      }
    })
    placed.push({ id: node.id, text, ...best.rect, align: best.align })
  }
  return placed
}

export function seededRandom(seed: number): () => number {
  let state = seed >>> 0
  return () => {
    state = (state + 0x6d2b79f5) >>> 0
    let t = state
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

export function nodeRadius(type: string): number {
  if (type === "insight" || type === "decision") return 15
  if (type === "goal" || type === "skill") return 13
  return 11
}

export function effectiveRadius(node: { type: string; importance?: number }): number {
  const importance = node.importance ?? 0.5
  return nodeRadius(node.type) * (0.9 + importance * 0.2)
}

export function placeNodes(
  base: GraphNodeBase[],
  width: number,
  height: number,
  seed = 7,
): GraphNode[] {
  const random = seededRandom(seed)
  const innerWidth = Math.max(width * 0.7, 200)
  const innerHeight = Math.max(height * 0.7, 200)
  return base.map((node) => ({
    ...node,
    x: width * 0.15 + random() * innerWidth,
    y: height * 0.15 + random() * innerHeight,
    vx: 0,
    vy: 0,
    radius: effectiveRadius(node),
  }))
}

export function clusterAnchors(
  clusters: GraphCluster[],
  width: number,
  height: number,
): Map<string, { x: number; y: number }> {
  const anchors = new Map<string, { x: number; y: number }>()
  if (!clusters.length) return anchors
  const columns = Math.ceil(Math.sqrt(clusters.length))
  const rows = Math.ceil(clusters.length / columns)
  const marginX = Math.min(width * 0.2, 150)
  const marginY = Math.min(height * 0.2, 110)
  const cellW = Math.max((width - marginX * 2) / columns, 80)
  const cellH = Math.max((height - marginY * 2) / rows, 80)
  clusters.forEach((cluster, index) => {
    const column = index % columns
    const row = Math.floor(index / columns)
    const random = seededRandom(31 + index)
    anchors.set(cluster.id, {
      x: marginX + cellW * (column + 0.5) + (random() - 0.5) * cellW * 0.18,
      y: marginY + cellH * (row + 0.5) + (random() - 0.5) * cellH * 0.18,
    })
  })
  return anchors
}

export function placeClusterNodes(
  base: GraphNodeBase[],
  clusters: GraphCluster[],
  width: number,
  height: number,
  seed = 11,
): GraphNode[] {
  const random = seededRandom(seed)
  const anchors = clusterAnchors(clusters, width, height)
  const anchorById = new Map<string, { x: number; y: number }>()
  for (const cluster of clusters) {
    const anchor = anchors.get(cluster.id)
    if (anchor) {
      for (const nodeId of cluster.node_ids) anchorById.set(nodeId, anchor)
    }
  }
  const indexByAnchor = new Map<string, number>()
  return base.map((node) => {
    const anchor = anchorById.get(node.id)
    const radius = effectiveRadius(node)
    const fallback: GraphNode = {
      ...node,
      x: width * 0.15 + random() * width * 0.7,
      y: height * 0.15 + random() * height * 0.7,
      vx: 0,
      vy: 0,
      radius,
    }
    if (!anchor) return fallback
    const key = `${anchor.x.toFixed(1)}:${anchor.y.toFixed(1)}`
    const indexInCluster = indexByAnchor.get(key) ?? 0
    indexByAnchor.set(key, indexInCluster + 1)
    const layer = TYPE_LAYER[node.type] ?? 2.5
    const x = anchor.x + (indexInCluster % 3 - 1) * 64 + (random() - 0.5) * 20
    const y = anchor.y + (layer - 2) * 72 + Math.floor(indexInCluster / 3) * 28 + (random() - 0.5) * 14
    return {
      ...node,
      x: Math.max(FORCES.margin, Math.min(width - FORCES.margin, x)),
      y: Math.max(FORCES.margin, Math.min(height - FORCES.margin, y)),
      vx: 0,
      vy: 0,
      radius,
    }
  })
}

export function forceLayout(
  nodes: GraphNode[],
  edges: GraphEdge[],
  options: GraphLayoutOptions,
): GraphNode[] {
  const width = Math.max(options.width, 200)
  const height = Math.max(options.height, 200)
  const count = nodes.length
  const maxIterations =
    options.iterations ??
    Math.max(40, Math.min(260, Math.floor(8_000_000 / Math.max(count * count, 1))))
  const indexById = new Map<string, number>()
  nodes.forEach((node, index) => indexById.set(node.id, index))
  const clusterCenters = options.clusterCenters
  const clusterStrength = options.clusterStrength ?? 0.04

  for (let iteration = 0; iteration < maxIterations; iteration += 1) {
    const alpha = 1 - iteration / maxIterations
    for (let i = 0; i < count; i += 1) {
      const a = nodes[i]
      a.vx = 0
      a.vy = 0
      for (let j = 0; j < count; j += 1) {
        if (i === j) continue
        const b = nodes[j]
        let dx = a.x - b.x
        let dy = a.y - b.y
        const distance = Math.max(Math.hypot(dx, dy), 1)
        let force = (FORCES.repulsion * alpha) / (distance * distance)
        const minDistance = a.radius + b.radius + FORCES.collisionPadding
        if (distance < minDistance) {
          force += (minDistance - distance) * 0.55 * alpha
        }
        dx /= distance
        dy /= distance
        a.vx += dx * force
        a.vy += dy * force
      }
    }
    for (const edge of edges) {
      const aIndex = indexById.get(edge.source)
      const bIndex = indexById.get(edge.target)
      if (aIndex === undefined || bIndex === undefined) continue
      const a = nodes[aIndex]
      const b = nodes[bIndex]
      let dx = a.x - b.x
      let dy = a.y - b.y
      const distance = Math.max(Math.hypot(dx, dy), 1)
      // Derived links should keep the network legible without pulling every
      // inferred relation into one dense ball. Explicit links retain the full
      // spring; inferred links provide only a light connective suggestion.
      const springLength = edge.derived ? FORCES.springLength * 0.82 : FORCES.springLength
      const springStrength = edge.derived ? FORCES.springStrength * 0.32 : FORCES.springStrength
      const displacement = (distance - springLength) * springStrength * alpha
      dx /= distance
      dy /= distance
      a.vx -= dx * displacement
      a.vy -= dy * displacement
      b.vx += dx * displacement
      b.vy += dy * displacement
    }
    for (const node of nodes) {
      node.vx += (width / 2 - node.x) * FORCES.gravity * alpha
      node.vy += (height / 2 - node.y) * FORCES.gravity * alpha
      const anchor = clusterCenters?.get(node.clusterId ?? "")
      if (anchor) {
        node.vx += (anchor.x - node.x) * clusterStrength * alpha
        node.vy += (anchor.y - node.y) * clusterStrength * alpha
      }
      const speed = Math.hypot(node.vx, node.vy)
      if (speed > FORCES.velocityCap) {
        node.vx = (node.vx / speed) * FORCES.velocityCap
        node.vy = (node.vy / speed) * FORCES.velocityCap
      }
      node.x += node.vx * FORCES.damping
      node.y += node.vy * FORCES.damping
      node.x = Math.max(FORCES.margin, Math.min(width - FORCES.margin, node.x))
      node.y = Math.max(FORCES.margin, Math.min(height - FORCES.margin, node.y))
    }
  }
  return nodes
}

export function totalKineticEnergy(nodes: GraphNode[]): number {
  return nodes.reduce((sum, node) => sum + node.vx * node.vx + node.vy * node.vy, 0)
}
