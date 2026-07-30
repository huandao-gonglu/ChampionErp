import { API_REQUEST_TIMEOUT_MS } from '@/api/client'
import type { DraftDetail, MarketplaceTargetSite, Product } from '@/types/workflow'
import type { AuthResult } from './normalizers'
import { asRecord, getString } from './normalizers'

const IMAGE_TRANSLATE_TIMEOUT_PER_IMAGE_MS = API_REQUEST_TIMEOUT_MS

export function imageTranslateTimeoutMs(imageIds: string[]): number {
  return IMAGE_TRANSLATE_TIMEOUT_PER_IMAGE_MS * Math.max(imageIds.length, 1)
}

export function requiredProductId(product: Product, action = '继续操作'): string {
  const productId = product.productId.trim()
  if (!productId) throw new Error(`请先保存或加载商品后再${action}`)
  return productId
}

export function requiredDraftTarget(draft: DraftDetail, target: MarketplaceTargetSite, action = '继续操作') {
  const draftId = String(draft.draftId || '').trim()
  if (!draftId) throw new Error(`请先从草稿箱选择草稿后再${action}`)
  const platform = String(target.platform || '').trim()
  const site = String(target.site || '').trim()
  if (!platform || !site) throw new Error(`当前草稿没有可${action}的目标站点`)
  return { draft_id: draftId, platform, site }
}

export function normalizeAuthResult(value: unknown): AuthResult {
  const record = asRecord(value)
  const explanation = asRecord(record.auth_explanation)
  const explanationTitle = getString(explanation, ['title'])
  const explanationMessage = getString(explanation, ['plain_message'])
  return {
    ok: record.ok !== false,
    message: getString(record, ['message', 'status'], record.ok === false ? '失败' : '成功'),
    error: explanationTitle || explanationMessage || getString(record, ['error', 'error_message']),
    errorCode: getString(record, ['error_code'], getString(explanation, ['code'])),
    nextAction: getString(record, ['next_action'], getString(explanation, ['next_action'])),
    raw: record,
  }
}
