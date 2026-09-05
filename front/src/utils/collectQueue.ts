import type { CollectBatchRow, CollectBatchStatus } from '@/types/workflow'

export const collectBatchStatusLabels: Record<CollectBatchStatus, string> = {
  waiting_verification: '等待验证', pending: '未开始', running: '正在采集', success: '采集完成', failed: '采集失败',
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

export function createCollectBatchRow(url: string, id: string = crypto.randomUUID()): CollectBatchRow {
  return {
    id, url, status: 'pending', platform: '', ok: false, title: '', image: '',
    error: '', errorCode: '', nextAction: '', productId: '',
  }
}

/** 先校验整批输入，错误时保留原列表和待添加文本。 */
export function appendCollectUrls(rows: CollectBatchRow[], input: string): { rows: CollectBatchRow[]; added: number; duplicates: number } {
  const values = input.trim().split(/\s+/).filter(Boolean)
  if (!values.length) throw new Error('请先填写商品链接，每行一个。')
  const urls = values.map((value, index) => {
    try {
      return normalizeCollectUrl(value)
    } catch (error) {
      throw new Error(`第 ${index + 1} 个链接无效：${(error as Error).message}`)
    }
  })
  const seen = new Set(rows.map((row) => row.url))
  const next = [...rows]
  let duplicates = 0
  for (const url of urls) {
    if (seen.has(url)) { duplicates++; continue }
    seen.add(url)
    next.push(createCollectBatchRow(url))
  }
  return { rows: next, added: next.length - rows.length, duplicates }
}

export const COLLECT_QUEUE_STORAGE_KEY = 'champion-erp.collect-queue.v1'

export function restoreCollectQueue(value: string | null): CollectBatchRow[] {
  try {
    const saved: unknown = JSON.parse(value || '[]')
    if (!Array.isArray(saved)) return []
    const rows: CollectBatchRow[] = []
    for (const item of saved) {
      if (!item || typeof item !== 'object' || typeof item.url !== 'string') continue
      let url: string
      try { url = normalizeCollectUrl(item.url) } catch { continue }
      if (rows.some((row) => row.url === url)) continue
      const row = createCollectBatchRow(url)
      for (const key of ['platform', 'title', 'image', 'error', 'errorCode', 'nextAction', 'productId'] as const) {
        if (typeof item[key] === 'string') row[key] = item[key]
      }
      row.status = item.status === 'success' ? 'success' : ['failed', 'running'].includes(item.status) ? 'failed' : 'pending'
      if (item.status === 'waiting_verification' && typeof item.verification?.browserTabId === 'string'
        && item.verification.browserTabId && item.verification.sourceUrl === url) {
        row.status = 'waiting_verification'
        row.verification = { browserTabId: item.verification.browserTabId, sourceUrl: url, platform: String(item.verification.platform || '') }
        row.error = ''
        row.nextAction = '请保留原商品标签页，点击开始采集继续等待验证。'
      }
      row.ok = row.status === 'success'
      if (item.status === 'running') {
        row.error = '页面关闭或刷新，未确认采集结果。请先检查商品库，再决定是否重试。'
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
