import { apiClient } from './client'
import { normalizeProductMutation, type ProductMutationResponse } from './workflow/normalizers'
import type { ImageBatchSnapshot } from './imageBatches'
import type { DraftDetail } from '@/types/workflow'

export interface DraftImageState extends ProductMutationResponse { batch: ImageBatchSnapshot | null }
function state(data: Record<string, unknown>): DraftImageState {
  return { ...normalizeProductMutation(data), batch: (data.image_batch as ImageBatchSnapshot | null) || null }
}
export async function fetchDraftImageState(draftId: string, requestId = '') {
  const { data } = await apiClient.post('/api/draft-images/state', { draft_id: draftId, request_id: requestId })
  return state(data)
}
export async function processDraftImages(draft: DraftDetail, operation: 'translate' | 'edit', ids: string[], prompt: string, requestId: string) {
  const { data } = await apiClient.post('/api/draft-images/process', {
    product_id: draft.productId, draft_id: draft.draftId, updated_at: draft.updatedAt,
    request_id: requestId, operation, source_image_ids: [...ids], prompt,
  })
  return state(data)
}
