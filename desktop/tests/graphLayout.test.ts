import { describe, expect, it } from "vitest"

import {
  clusterAnchors,
  forceLayout,
  layoutLabels,
  placeClusterNodes,
  placeNodes,
  effectiveRadius,
  nodeRadius,
  shortLabel,
  seededRandom,
  totalKineticEnergy,
  type GraphCluster,
  type GraphEdge,
  type GraphNodeBase,
} from "@/graph/layout"

const baseNodes: GraphNodeBase[] = [
  {
    id: "a", type: "goal", statement: "Enter the AI industry.", reason: null,
    namespace: "general", sensitivity: "personal", epistemic_state: "asserted",
    status: "active", revision: 1, payload: {},
    created_at: "2026-07-31T00:00:00Z", updated_at: "2026-07-31T00:00:00Z",
  },
  {
    id: "b", type: "skill", statement: "Master Python.", reason: null,
    namespace: "general", sensitivity: "personal", epistemic_state: "asserted",
    status: "active", revision: 1, payload: {},
    created_at: "2026-07-31T00:00:00Z", updated_at: "2026-07-31T00:00:00Z",
  },
  {
    id: "c", type: "insight", statement: "A memory layer is needed.", reason: null,
    namespace: "general", sensitivity: "personal", epistemic_state: "provisional",
    status: "active", revision: 1, payload: {},
    created_at: "2026-07-31T00:00:00Z", updated_at: "2026-07-31T00:00:00Z",
  },
  {
    id: "d", type: "decision", statement: "Prefer structured queries.", reason: null,
    namespace: "general", sensitivity: "personal", epistemic_state: "asserted",
    status: "active", revision: 1, payload: {},
    created_at: "2026-07-31T00:00:00Z", updated_at: "2026-07-31T00:00:00Z",
  },
]

const edges: GraphEdge[] = [
  { source: "a", target: "b", type: "related_to", origin: "derived_co_source", derived: true },
  { source: "b", target: "c", type: "related_to", origin: "derived_co_source", derived: true },
  { source: "c", target: "d", type: "supports", origin: "user_reviewed", derived: false },
]

describe("graph layout", () => {
  it("keeps node radii compact for dense desktop graphs", () => {
    expect(nodeRadius("fact")).toBe(11)
    expect(nodeRadius("goal")).toBe(13)
    expect(nodeRadius("insight")).toBe(15)
    expect(effectiveRadius({ type: "insight", importance: 1 })).toBeLessThanOrEqual(17)
  })

  it("seeded random is deterministic", () => {
    const first = seededRandom(42)
    const second = seededRandom(42)
    expect([first(), first(), first()]).toEqual([second(), second(), second()])
  })

  it("places nodes inside the canvas with finite coordinates", () => {
    const nodes = placeNodes(baseNodes, 800, 600)
    for (const node of nodes) {
      expect(Number.isFinite(node.x)).toBe(true)
      expect(Number.isFinite(node.y)).toBe(true)
      expect(node.x).toBeGreaterThanOrEqual(0)
      expect(node.x).toBeLessThanOrEqual(800)
      expect(node.y).toBeGreaterThanOrEqual(0)
      expect(node.y).toBeLessThanOrEqual(600)
    }
  })

  it("force layout keeps positions finite and inside the canvas", () => {
    const nodes = forceLayout(placeNodes(baseNodes, 800, 600), edges, {
      width: 800,
      height: 600,
      iterations: 200,
    })
    for (const node of nodes) {
      expect(Number.isFinite(node.x)).toBe(true)
      expect(Number.isFinite(node.y)).toBe(true)
      expect(node.x).toBeGreaterThanOrEqual(0)
      expect(node.x).toBeLessThanOrEqual(800)
      expect(node.y).toBeGreaterThanOrEqual(0)
      expect(node.y).toBeLessThanOrEqual(600)
    }
    const distances: number[] = []
    for (let i = 0; i < nodes.length; i += 1) {
      for (let j = i + 1; j < nodes.length; j += 1) {
        distances.push(Math.hypot(nodes[i].x - nodes[j].x, nodes[i].y - nodes[j].y))
      }
    }
    expect(Math.max(...distances)).toBeGreaterThan(30)
  })

  it("force layout converges and is deterministic", () => {
    const run = (iterations: number) => {
      const nodes = placeNodes(baseNodes, 800, 600)
      forceLayout(nodes, edges, { width: 800, height: 600, iterations })
      return { energy: totalKineticEnergy(nodes), positions: nodes.map((n) => [n.x, n.y]) }
    }
    const early = run(20)
    const settled = run(200)
    const again = run(200)
    expect(settled.energy).toBeLessThan(early.energy)
    expect(settled.positions).toEqual(again.positions)
  })

  it("cluster anchors are deterministic and spread across the canvas", () => {
    const clusters: GraphCluster[] = [
      { id: "c0", label: "AI", size: 3, node_ids: ["a", "b", "c"] },
      { id: "c1", label: "Python", size: 1, node_ids: ["d"] },
    ]
    const first = clusterAnchors(clusters, 800, 600)
    const second = clusterAnchors(clusters, 800, 600)
    expect([...first.entries()]).toEqual([...second.entries()])
    const points = [...first.values()]
    const distances = Math.hypot(points[0].x - points[1].x, points[0].y - points[1].y)
    expect(distances).toBeGreaterThan(200)
  })

  it("cluster placement groups members near their anchor", () => {
    const clusters: GraphCluster[] = [
      { id: "c0", label: "AI", size: 3, node_ids: ["a", "b", "c"] },
      { id: "c1", label: "Python", size: 1, node_ids: ["d"] },
    ]
    const positioned = placeClusterNodes(baseNodes, clusters, 800, 600)
    const anchors = clusterAnchors(clusters, 800, 600)
    const anchorA = anchors.get("c0")!
    const anchorD = anchors.get("c1")!
    const memberDistances = ["a", "b", "c"].map(
      (id) => Math.hypot(
        positioned.find((n) => n.id === id)!.x - anchorA.x,
        positioned.find((n) => n.id === id)!.y - anchorA.y,
      ),
    )
    expect(Math.max(...memberDistances)).toBeLessThan(220)
    const otherDistance = Math.hypot(
      positioned.find((n) => n.id === "d")!.x - anchorA.x,
      positioned.find((n) => n.id === "d")!.y - anchorA.y,
    )
    expect(otherDistance).toBeGreaterThan(Math.max(...memberDistances))
  })

  it("cluster force keeps nodes inside canvas and near cluster anchors", () => {
    const clusters: GraphCluster[] = [
      { id: "c0", label: "AI", size: 3, node_ids: ["a", "b", "c"] },
      { id: "c1", label: "Python", size: 1, node_ids: ["d"] },
    ]
    const clusterCenters = clusterAnchors(clusters, 800, 600)
    const nodes = placeClusterNodes(baseNodes, clusters, 800, 600)
    forceLayout(nodes, edges, {
      width: 800,
      height: 600,
      iterations: 200,
      clusterCenters,
    })
    for (const node of nodes) {
      expect(Number.isFinite(node.x)).toBe(true)
      expect(node.x).toBeGreaterThanOrEqual(0)
      expect(node.x).toBeLessThanOrEqual(800)
    }
    const anchorA = clusterCenters.get("c0")!
    const memberDistance = Math.hypot(
      nodes.find((n) => n.id === "a")!.x - anchorA.x,
      nodes.find((n) => n.id === "a")!.y - anchorA.y,
    )
    expect(memberDistance).toBeLessThan(300)
  })

  it("shortens labels without losing whitespace readability", () => {
    expect(shortLabel("  A   memory\nstatement  ", 40)).toBe("A memory statement")
    expect(shortLabel("A long memory statement", 10)).toBe("A long me…")
  })

  it("places every label deterministically and avoids simple collisions", () => {
    const labelNodes = baseNodes.map((node, index) => ({
      id: node.id,
      x: 180 + index * 145,
      y: 220 + (index % 2) * 70,
      radius: 8,
      statement: node.statement,
      importance: index === 0 ? 1 : 0.5,
    }))
    const options = { width: 800, height: 600, zoom: 1, offsetX: 0, offsetY: 0 }
    const first = layoutLabels(labelNodes, options)
    const second = layoutLabels(labelNodes, options)
    expect(first).toEqual(second)
    expect(first).toHaveLength(labelNodes.length)
    expect(new Set(first.map((label) => label.id)).size).toBe(labelNodes.length)
    for (const label of first) {
      expect(label.text.length).toBeLessThanOrEqual(26)
      expect(label.x + label.width).toBeGreaterThanOrEqual(0)
      expect(label.y + label.height).toBeGreaterThanOrEqual(0)
    }
    for (let i = 0; i < first.length; i += 1) {
      for (let j = i + 1; j < first.length; j += 1) {
        const a = first[i]
        const b = first[j]
        const overlap = Math.max(0, Math.min(a.x + a.width, b.x + b.width) - Math.max(a.x, b.x))
          * Math.max(0, Math.min(a.y + a.height, b.y + b.height) - Math.max(a.y, b.y))
        expect(overlap).toBe(0)
      }
    }
  })
})
