import type { UnknownRecord } from '@/types/workflow'

const sensitiveConfigKeys = new Set([
  'access_token',
  'alibaba_cookie',
  'api_key',
  'api_token',
  'app_id',
  'app_key',
  'app_secret',
  'authorization',
  'bearer_token',
  'client_secret',
  'code_verifier',
  'cookie',
  'password',
  'private_key',
  'refresh_token',
  'secret',
  'source_key',
  'token',
])

const sensitiveConfigKeySuffixes = [
  '_api_key',
  '_app_id',
  '_app_key',
  '_cookie',
  '_password',
  '_private_key',
  '_secret',
  '_source_key',
  '_token',
]

export function normalizeConfigKey(key: unknown): string {
  return String(key ?? '')
    .trim()
    .replace(/(.)([A-Z][a-z]+)/g, '$1_$2')
    .replace(/([a-z0-9])([A-Z])/g, '$1_$2')
    .replace(/[^A-Za-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .toLowerCase()
}

export function isSensitiveConfigKey(key: unknown): boolean {
  const normalized = normalizeConfigKey(key)
  if (!normalized || normalized.startsWith('masked_')) return false
  return sensitiveConfigKeys.has(normalized)
    || sensitiveConfigKeySuffixes.some((suffix) => normalized.endsWith(suffix))
}

function sanitizeConfigValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map((item) => sanitizeConfigValue(item))
  if (!value || typeof value !== 'object') return value
  return Object.fromEntries(
    Object.entries(value).map(([key, item]) => [
      key,
      isSensitiveConfigKey(key) ? '' : sanitizeConfigValue(item),
    ]),
  )
}

/**
 * Pinia 只持有公共配置和掩码摘要；可提交凭据不得进入全局响应式状态。
 */
export function sanitizePublicAppConfig(value: unknown): UnknownRecord {
  const sanitized = sanitizeConfigValue(value)
  return sanitized && typeof sanitized === 'object' && !Array.isArray(sanitized)
    ? sanitized as UnknownRecord
    : {}
}
