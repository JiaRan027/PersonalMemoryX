import { describe, expect, it } from "vitest"

import { importNoticeText, summarizeImports, type ImportSummary } from "@/recognition/importSummary"

describe("summarizeImports", () => {
  it("aggregates per-source import responses", () => {
    const summary = summarizeImports([
      { total: 12, ingested: 1, duplicate: 0, processed: 11, skipped: 0, failed: 0, files: [] },
      { total: 1, ingested: 0, duplicate: 0, processed: 0, skipped: 1, failed: 0, files: [] },
    ])
    expect(summary).toEqual({
      sourcesWithNewMemories: 1,
      processedEmpty: 11,
      duplicate: 0,
      skipped: 1,
      failed: 0,
      totalFiles: 13,
    })
  })

  it("produces honest aggregate notice text", () => {
    const summary: ImportSummary = {
      sourcesWithNewMemories: 1,
      processedEmpty: 11,
      duplicate: 0,
      skipped: 1,
      failed: 1,
      totalFiles: 13,
    }
    const text = importNoticeText(summary, 1)
    expect(text).toContain("新增 1 份来源的记忆")
    expect(text).toContain("11 份已处理但未提取出候选记忆")
    expect(text).toContain("1 份超限跳过")
    expect(text).toContain("2 份导入失败")
  })

  it("returns latest-state text when nothing changed", () => {
    expect(importNoticeText(summarizeImports([]), 0)).toBe("来源已是最新状态，没有新增内容。")
  })
})
