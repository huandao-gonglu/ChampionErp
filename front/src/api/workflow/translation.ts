import { apiClient } from '@/api/client'
import { asRecord, ensureOk } from '@/api/workflow/normalizers'

export type TextTranslationMap = Record<string, string>

const TRANSLATION_BATCH_SIZE = 100

async function translateTextBatch(targetLanguage: string, content: TextTranslationMap): Promise<TextTranslationMap> {
  const response = await apiClient.post('/api/text-translate', {
    target_language: targetLanguage,
    content,
  })
  const data = asRecord(response.data)
  ensureOk(data, '翻译文本失败')
  const rawTranslations = asRecord(data.translations)
  const expectedKeys = Object.keys(content)
  if (
    Object.keys(rawTranslations).length !== expectedKeys.length
    || expectedKeys.some((key) => typeof rawTranslations[key] !== 'string' || !String(rawTranslations[key]).trim())
  ) {
    throw new Error('翻译结果与请求内容不一致')
  }
  return Object.fromEntries(expectedKeys.map((key) => [key, String(rawTranslations[key]).trim()]))
}

export async function translateText(targetLanguage: string, content: TextTranslationMap): Promise<TextTranslationMap> {
  const language = targetLanguage.trim()
  if (!language) throw new Error('缺少目标翻译语言')
  const entries = Object.entries(content)
  if (!entries.length) return {}
  if (entries.some(([key, value]) => !key.trim() || typeof value !== 'string' || !value.trim())) {
    throw new Error('翻译内容必须使用非空 key 和非空字符串 value')
  }
  const translations: TextTranslationMap = {}
  for (let index = 0; index < entries.length; index += TRANSLATION_BATCH_SIZE) {
    Object.assign(
      translations,
      await translateTextBatch(language, Object.fromEntries(entries.slice(index, index + TRANSLATION_BATCH_SIZE))),
    )
  }
  return translations
}
