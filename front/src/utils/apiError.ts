export interface NormalizedApiError {
  message: string
  status?: number
  code?: string
  raw?: unknown
}

export function normalizeApiError(error: unknown, fallback = '请求失败'): NormalizedApiError {
  if (error instanceof Error) return { message: error.message || fallback, raw: error }
  if (typeof error === 'string') return { message: error || fallback, raw: error }
  if (error && typeof error === 'object') {
    const record = error as Record<string, unknown>
    return {
      message: String(record.message || record.error || fallback),
      code: record.code ? String(record.code) : undefined,
      status: typeof record.status === 'number' ? record.status : undefined,
      raw: error,
    }
  }
  return { message: fallback, raw: error }
}
