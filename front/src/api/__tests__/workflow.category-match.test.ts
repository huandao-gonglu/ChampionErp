import { beforeEach, describe, expect, it, vi } from 'vitest'
import { apiClient } from '@/api/client'
import { matchCategory } from '@/api/workflow/publishing'
import { createEmptyDraftDetail } from '@/constants/initialState'

vi.mock('@/api/client', () => ({
  API_REQUEST_TIMEOUT_MS: 30000,
  apiClient: {
    post: vi.fn(),
  },
}))

describe('category.product_match API contract', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('normalizes candidates and keeps the verified selection first for manual confirmation', async () => {
    const draft = createEmptyDraftDetail('mercadolibre')
    draft.draftId = 'draft-1'
    draft.productId = 'product-1'
    const target = {
      platform: 'mercadolibre',
      site: 'MLM',
      language: 'es-MX',
      marketCurrency: 'MXN',
      listingCurrency: 'MXN',
    }
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: {
        ok: true,
        status: 'completed',
        selected_category_id: 'MLM-FAN',
        candidates: [
          {
            category_id: 'MLM-USB',
            name: 'Accesorios USB',
            path_segments: ['Electrónica', 'Accesorios USB'],
          },
          {
            category_id: 'MLM-FAN',
            name: 'Ventiladores',
            path_segments: ['Hogar', 'Ventiladores'],
          },
        ],
        query: 'ventilador',
        decision: {
          confidence_band: 'high',
          model_confidence: 0.95,
          decision_score: 0.86,
          abstained: false,
          evidence: ['主体一致'],
          search_count: 1,
        },
        failure: null,
        trace: {
          conversation_id: 'aic-1',
          task_run_id: 'task-1',
        },
      },
    })

    const result = await matchCategory(draft, target)

    expect(apiClient.post).toHaveBeenCalledWith('/api/category-match', expect.objectContaining({
      draft_id: 'draft-1',
      platform: 'mercadolibre',
      site: 'MLM',
      language: 'es-MX',
    }))
    expect(result.status).toBe('completed')
    expect(result.query).toBe('ventilador')
    expect(result.candidates.map((item) => item.id)).toEqual(['MLM-FAN', 'MLM-USB'])
    expect(result.candidates[0]?.path).toBe('Hogar / Ventiladores')
    expect(result.decision.confidenceBand).toBe('high')
    expect(result.trace.taskRunId).toBe('task-1')
  })
})
