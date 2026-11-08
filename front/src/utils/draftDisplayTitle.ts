import type { DraftIndexItem, DraftProductContext } from '@/types/workflow'

export function draftListTitle(item: DraftIndexItem): string {
  return item.productTitle || item.productId || item.draftId
}

export function draftContextTitle(product: DraftProductContext, draftId: string): string {
  return product.sourceTitle || product.title || product.productId || draftId
}
