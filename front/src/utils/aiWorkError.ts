import type { AiWorkEvent } from '@/types/aiWork'

type AiWorkError = Pick<AiWorkEvent, 'code' | 'message'>

export function formatAiWorkError(error?: AiWorkError | null): string {
  const code = String(error?.code || '').trim()
  const message = String(error?.message || '').trim()
  if (code && message) return `${code}：${message}`
  return message || code || 'AI 执行失败'
}
