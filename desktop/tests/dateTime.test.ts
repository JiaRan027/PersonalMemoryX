import { describe, expect, it } from "vitest"

import { formatLocalDateTime } from "@/utils/dateTime"

describe("formatLocalDateTime", () => {
  it("treats legacy timestamps without an offset as UTC and shows the target offset", () => {
    const rendered = formatLocalDateTime("2026-08-01T19:04:59", "Asia/Shanghai")

    expect(rendered).toContain("2026/08/02")
    expect(rendered).toContain("03:04:59")
    expect(rendered).toContain("GMT+8")
  })

  it("preserves explicit offsets when converting to the display timezone", () => {
    const rendered = formatLocalDateTime("2026-08-02T03:04:59+08:00", "Asia/Shanghai")

    expect(rendered).toContain("03:04:59")
    expect(rendered).toContain("GMT+8")
  })
})
