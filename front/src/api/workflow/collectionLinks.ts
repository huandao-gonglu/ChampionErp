import { apiClient } from '@/api/client'
import { submitCollection } from './collectionReceipt'
import type { BackendCollectionLink, BackendCollectionLinkEvent } from '@/types/workflow.generated'

export type CollectionLink = BackendCollectionLink
export type LinkMode = CollectionLink['mode']
export type LinkTarget = Pick<CollectionLink, 'id' | 'revision'>
export type LinkEvent = BackendCollectionLinkEvent

export async function listLinks() {
  const { data } = await apiClient.get<{ items: CollectionLink[] }>('/api/business-queries/collection.links')
  return data.items
}

export async function linkHistory(id: string) {
  const { data } = await apiClient.get<{ items: LinkEvent[] }>('/api/business-queries/collection.history', { params: { id } })
  return data.items
}

export async function manageLinks(body: Record<string, unknown>) {
  const { data } = await apiClient.post('/api/collection-links/manage', body)
  return data as { added?: number; duplicates?: number }
}

export async function runLink({ id, revision }: LinkTarget): Promise<CollectionLink> {
  const { data } = await submitCollection('/api/collection-links/run', { id, revision })
  return data.item
}

export async function verifyLink({ id, revision }: LinkTarget, signal: AbortSignal) {
  const { data } = await apiClient.post('/api/collection-links/verify', { id, revision }, { signal })
  if (!data.ok) throw new Error(data.message || '检查商品页验证状态失败。')
  return data.status as 'waiting_verification' | 'loading' | 'ready' | 'unavailable'
}
