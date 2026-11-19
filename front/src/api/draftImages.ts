import { apiClient } from './client'
import { normalizeDraftMutation, normalizeProductMutation, type DraftMutationResponse, type ProductMutationResponse } from './workflow/normalizers'
import type { ImageBatchSnapshot } from './imageBatches'
import type { DraftDetail } from '@/types/workflow'
import type { BackendImageSetPlanPayload } from '@/types/workflow.generated'

export type ImageSetPlan = BackendImageSetPlanPayload

export interface DraftImageState extends ProductMutationResponse { batch: ImageBatchSnapshot | null }
function state(data: Record<string, unknown>): DraftImageState {
  return { ...normalizeProductMutation(data), batch: (data.image_batch as ImageBatchSnapshot | null) || null }
}
export type DraftImagePoll = DraftImageState | ((DraftMutationResponse | { unchanged: true }) & { batch: ImageBatchSnapshot | null })
export function fetchDraftImageState(draftId: string, requestId?: string): Promise<DraftImageState>
export function fetchDraftImageState(draftId: string, requestId: string, version: string): Promise<DraftImagePoll>
export async function fetchDraftImageState(draftId: string, requestId = '', version = ''): Promise<DraftImagePoll> {
  const { data } = await apiClient.post('/api/draft-images/state', { draft_id: draftId, request_id: requestId,
    ...(version ? { known_updated_at: version } : {}) })
  if (version) {
    const batch: ImageBatchSnapshot | null = data.image_batch ?? null
    if (data.unchanged === true) return { unchanged: true, batch }
    return { ...normalizeDraftMutation(data), batch }
  }
  return state(data)
}
export async function processDraftImages(draft: DraftDetail, operation: 'translate' | 'edit', ids: string[], prompt: string, requestId: string) {
  const { data } = await apiClient.post('/api/draft-images/process', {
    product_id: draft.productId, draft_id: draft.draftId, updated_at: draft.updatedAt,
    request_id: requestId, operation, source_image_ids: [...ids], prompt,
  })
  return state(data)
}

export async function planDraftImageSet(draft: DraftDetail, ids: string[], count: number | undefined, prompt: string, skuIds: string[] = []) {
  const { data } = await apiClient.post<{ ok: boolean; plan: ImageSetPlan; updated_at: string }>('/api/draft-images/plan-set', {
    product_id: draft.productId, draft_id: draft.draftId, updated_at: draft.updatedAt,
    source_image_ids: ids, count, prompt, sku_ids: skuIds,
  })
  return data
}

export async function generateDraftImageSet(draft: DraftDetail, requestId: string, options: {
  plan?: ImageSetPlan; updatedAt: string; baseRequestId?: string; retryPageIds?: string[]
}) {
  const { data } = await apiClient.post('/api/draft-images/generate-set', {
    product_id: draft.productId, draft_id: draft.draftId, request_id: requestId,
    updated_at: options.updatedAt, plan: options.plan,
    base_request_id: options.baseRequestId, retry_page_ids: options.retryPageIds,
  })
  return state(data)
}
