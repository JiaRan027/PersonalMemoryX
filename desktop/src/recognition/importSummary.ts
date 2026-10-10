import type { ImportResponse } from "@/api/client"
import { t } from "@/i18n"

export interface ImportSummary {
  sourcesWithNewMemories: number
  processedEmpty: number
  duplicate: number
  skipped: number
  failed: number
  totalFiles: number
}

export function summarizeImports(results: ImportResponse[]): ImportSummary {
  const summary: ImportSummary = {
    sourcesWithNewMemories: 0,
    processedEmpty: 0,
    duplicate: 0,
    skipped: 0,
    failed: 0,
    totalFiles: 0,
  }
  for (const result of results) {
    summary.sourcesWithNewMemories += result.ingested
    summary.processedEmpty += result.processed
    summary.duplicate += result.duplicate
    summary.skipped += result.skipped
    summary.failed += result.failed
    summary.totalFiles += result.total
  }
  return summary
}

export function importNoticeText(summary: ImportSummary, importErrors: number): string {
  const parts: string[] = []
  if (summary.sourcesWithNewMemories > 0) {
    parts.push(t("新增 {count} 份来源的记忆", { count: summary.sourcesWithNewMemories }))
  }
  if (summary.processedEmpty > 0) {
    parts.push(t("{count} 份已处理但未提取出候选记忆", { count: summary.processedEmpty }))
  }
  if (summary.duplicate > 0) {
    parts.push(t("{count} 份无新增内容", { count: summary.duplicate }))
  }
  if (summary.skipped > 0) {
    parts.push(t("{count} 份超限跳过", { count: summary.skipped }))
  }
  const failed = summary.failed + importErrors
  if (failed > 0) {
    parts.push(t("{count} 份导入失败", { count: failed }))
  }
  if (parts.length === 0) {
    return t("来源已是最新状态，没有新增内容。")
  }
  return `${parts.join(t("，"))}${t("。")}`
}
