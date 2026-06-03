export function formatDateTime(value: string | number | Date | undefined): string {
  if (!value) return '-'
  const date = value instanceof Date ? value : new Date(value)
  if (Number.isNaN(date.getTime())) return String(value)
  return date.toLocaleString()
}

export function formatPercent(value: number | undefined, digits = 0): string {
  if (!Number.isFinite(value)) return '-'
  return `${Number(value).toFixed(digits)}%`
}
