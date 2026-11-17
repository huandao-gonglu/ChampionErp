import type { CollectBatchStatus, CollectionVerification } from '@/types/workflow'

export const collectBatchStatusLabels: Record<CollectBatchStatus, string> = {
  pending: '未开始', running: '采集中', waiting_verification: '待验证', success: '已入采集箱', failed: '失败', unconfirmed: '结果待确认',
}

export function normalizeCollectUrl(value: string): string {
  const text = value.trim()
  let url: URL
  try {
    url = new URL(text)
  } catch {
    throw new Error('请输入完整的商品 URL，以 http:// 或 https:// 开头。')
  }
  if (!['http:', 'https:'].includes(url.protocol) || !url.hostname || url.username || url.password) {
    throw new Error('仅支持不含账号密码的 HTTP / HTTPS 商品链接。')
  }
  url.hash = ''
  return url.href
}

interface LegacyLink {
  url: string
  status: CollectBatchStatus
  title?: string
  image?: string
  productId?: string
  error?: string
  errorCode?: string
  nextAction?: string
  verification?: CollectionVerification
}

export const COLLECT_QUEUE_STORAGE_KEY = 'champion-erp.collect-queue.v1'

export function restoreCollectQueue(value: string | null): LegacyLink[] {
  try {
    const saved: unknown = JSON.parse(value || '[]')
    if (!Array.isArray(saved)) return []
    const rows: LegacyLink[] = []
    const seen = new Set<string>()
    for (const item of saved) {
      if (!item || typeof item !== 'object' || typeof item.url !== 'string') continue
      let url: string
      try { url = normalizeCollectUrl(item.url) } catch { continue }
      if (seen.has(url)) continue
      seen.add(url)
      const row: LegacyLink = { url, status: 'pending' }
      for (const key of ['title', 'image', 'error', 'errorCode', 'nextAction', 'productId'] as const) {
        if (typeof item[key] === 'string') row[key] = item[key]
      }
      row.status = item.status === 'success' ? 'success' : ['running', 'unconfirmed'].includes(item.status) || item.errorCode === 'COLLECT_RESULT_UNCONFIRMED' ? 'unconfirmed' : item.status === 'failed' ? 'failed' : 'pending'
      if (item.status === 'waiting_verification' && typeof item.verification?.browserTabId === 'string'
        && item.verification.browserTabId && item.verification.sourceUrl === url) {
        row.status = 'waiting_verification'
        row.verification = { browserTabId: item.verification.browserTabId, sourceUrl: url, platform: String(item.verification.platform || '') }
        row.error = ''
        row.nextAction = '请保留原商品标签页，点击开始采集继续等待验证。'
      }
      if (item.status === 'running') {
        row.error = '页面关闭或刷新，未确认采集结果。请先检查采集箱，再决定是否重试。'
        row.errorCode = 'COLLECT_RESULT_UNCONFIRMED'
        row.nextAction = ''
      }
      rows.push(row)
    }
    return rows
  } catch {
    return []
  }
}
