import { API_REQUEST_TIMEOUT_MS, apiClient } from '@/api/client'
import { asRecord, ensureOk } from '@/api/workflow/normalizers'

export type TextTranslationMap = Record<string, string>
const TRANSLATION_BATCH_SIZE = 100

export async function translateText(
  targetLanguage: string,
  content: TextTranslationMap,
  conversationId?: string,
  scope?: { draft_id: string; platform: string; site: string; category_id?: string },
): Promise<TextTranslationMap> {
  const language = targetLanguage.trim()
  if (!language) throw new Error('缺少目标翻译语言')
  const entries = Object.entries(content)
  if (!entries.length) return {}
  if (entries.some(([key, value]) => !key.trim() || typeof value !== 'string' || !value.trim())) {
    throw new Error('翻译内容必须使用非空 key 和非空字符串 value')
  }
  const batches = Math.ceil(entries.length / TRANSLATION_BATCH_SIZE)
  const response = await apiClient.post('/api/text-translate', {
    ...scope, target_language: language, content,
  }, {
    aiConversationId: conversationId,
    ...(batches > 1 ? { timeout: API_REQUEST_TIMEOUT_MS * batches } : {}),
  })
  const data = asRecord(response.data)
  ensureOk(data, '翻译文本失败')
  const translations = asRecord(data.translations)
  if (Object.keys(translations).length !== entries.length
      || entries.some(([key]) => typeof translations[key] !== 'string' || !String(translations[key]).trim())) {
    throw new Error('翻译结果与请求内容不一致')
  }
  return Object.fromEntries(entries.map(([key]) => [key, String(translations[key]).trim()]))
}
