export function safeText(value: unknown): string {
  return String(value ?? '').trim()
}
