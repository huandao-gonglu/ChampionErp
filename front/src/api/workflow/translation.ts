import { API_REQUEST_TIMEOUT_MS, apiClient } from '@/api/client'
import { asRecord, ensureOk, getBoolean, getString } from '@/api/workflow/normalizers'

export type TextTranslationMap = Record<string, string>
const TRANSLATION_BATCH_SIZE = 100

const CJK_RE = /[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]/
const PURE_NUMERIC_UNIT_RE = /^[\d\s.,/+\-×xX*%°℃℉()"'`~]+(?:mm|cm|m|km|mg|g|kg|ml|l|oz|lb|w|kw|v|mv|a|ma|mah|hz|khz|mhz|ghz|gb|mb|tb|kb|inch|in|ft|pcs|шт|см|мм|кг|г|мл|л|вт)?$/i
const SHORT_CODE_RE = /^(?:[A-Z0-9][A-Z0-9._\-/+]{0,15}|[0-9]{1,3}XL|X{0,3}[SML])$/

export function hasCjkText(value: string): boolean {
  return CJK_RE.test(String(value || ''))
}

/** 跳过已有中文、纯数字/规格单位及短型号/尺码编码，避免无意义翻译。 */
export function isNonTranslatableText(value: string): boolean {
  const trimmed = String(value || '').trim()
  if (!trimmed) return true
  if (hasCjkText(trimmed)) return true
  if (PURE_NUMERIC_UNIT_RE.test(trimmed)) return true
  if (trimmed.length <= 16 && SHORT_CODE_RE.test(trimmed)) return true
  return false
}

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
    ...scope,
    ...(scope?.platform ? { domain: scope.platform } : {}),
    target_language: language,
    content,
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

export interface SilentTranslationResult {
  translations: TextTranslationMap
  source: string
  degraded: boolean
}

/**
 * L3 静默非阻塞增量补翻：
 * - 不传 aiConversationId，不开启 AI 会话，不阻塞界面；
 * - 未配置翻译模型或调用失败时优雅降级，返回已命中的 L2 词典结果与空映射。
 */
export async function translateTextSilent(
  targetLanguage: string,
  content: TextTranslationMap,
  options: {
    domain?: string
    cacheOnly?: boolean
    scope?: { draft_id?: string; platform?: string; site?: string; category_id?: string }
  } = {},
): Promise<SilentTranslationResult> {
  const language = targetLanguage.trim()
  const entries = Object.entries(content).filter(([key, val]) => key.trim() && typeof val === 'string' && val.trim())
  if (!language || !entries.length) {
    return { translations: {}, source: 'cache', degraded: false }
  }
  const cleanContent = Object.fromEntries(entries.map(([key, val]) => [key, val.trim()]))
  const batches = Math.ceil(entries.length / TRANSLATION_BATCH_SIZE)
  try {
    const response = await apiClient.post('/api/text-translate', {
      ...options.scope,
      domain: options.domain || options.scope?.platform || 'general',
      target_language: language,
      content: cleanContent,
      cache_only: Boolean(options.cacheOnly),
      silent: true,
    }, {
      ...(batches > 1 ? { timeout: API_REQUEST_TIMEOUT_MS * batches } : {}),
    })
    const data = asRecord(response.data)
    if (data.ok === false) {
      return { translations: {}, source: 'cache', degraded: true }
    }
    const rawTranslations = asRecord(data.translations)
    const translations: TextTranslationMap = {}
    for (const [key] of entries) {
      const val = rawTranslations[key]
      if (typeof val === 'string' && val.trim()) {
        translations[key] = val.trim()
      }
    }
    return {
      translations,
      source: getString(data, ['source'], 'cache'),
      degraded: getBoolean(data, ['degraded']),
    }
  } catch {
    return { translations: {}, source: 'cache', degraded: true }
  }
}
