function hasExplicitTimeZone(value: string): boolean {
  return /(?:Z|[+-]\d{2}:\d{2})$/i.test(value)
}

export function formatLocalDateTime(
  value: string,
  timeZone = Intl.DateTimeFormat().resolvedOptions().timeZone,
): string {
  const normalized = hasExplicitTimeZone(value) ? value : `${value}Z`
  const date = new Date(normalized)
  if (Number.isNaN(date.getTime())) return value

  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
    timeZone,
    timeZoneName: "shortOffset",
  }).format(date)
}
