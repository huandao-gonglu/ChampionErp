import { beforeEach, describe, expect, it, vi } from 'vitest'
import { apiClient } from '@/api/client'
import { fetchPublishJobs } from '@/api/workflow/publishing'

vi.mock('@/api/client', () => ({
  API_REQUEST_TIMEOUT_MS: 30_000,
  apiClient: {
    get: vi.fn(),
    post: vi.fn(),
  },
}))

describe('发布队列 API', () => {
  beforeEach(() => vi.clearAllMocks())

  it('保留 CBT 发布任务的销售子市场摘要', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: {
        ok: true,
        items: [
          {
            job_id: '20260829-204353-9e0373b7',
            product_id: '50869d686a598917',
            draft_id: 'd5cc0d58cb7bd',
            status: 'failed',
            raw_status: 'completed',
            platforms: [{
              platform: 'mercadolibre',
              draft_id: 'd5cc0d58cb7bd',
              site: 'CBT',
              sites_to_sell: [{ site_id: 'MLU', logistic_type: 'remote' }],
              status: 'failed',
              stage: 'failed',
              error_code: 'MERCADOLIBRE_CATEGORY_MARKET_LOGISTICS_UNSUPPORTED',
              next_action: '请选择共享类目',
            }],
            error_code: 'MERCADOLIBRE_CATEGORY_MARKET_LOGISTICS_UNSUPPORTED',
            next_action: '请选择共享类目',
          },
          {
            job_id: '20260829-203906-86e8145e',
            product_id: '50869d686a598917',
            draft_id: 'd12fb1fe48cb6',
            status: 'success',
            raw_status: 'completed',
            platforms: [{
              platform: 'mercadolibre',
              draft_id: 'd12fb1fe48cb6',
              site: 'CBT',
              sites_to_sell: [{ site_id: 'MLA', logistic_type: 'remote' }],
              status: 'success',
              stage: 'finished',
            }],
          },
        ],
        next_cursor: '',
      },
    })

    const result = await fetchPublishJobs()

    expect(apiClient.get).toHaveBeenCalledWith('/api/publish-bus/jobs?limit=50')
    expect(result.items[0].platforms[0].sitesToSell).toEqual([
      { siteId: 'MLU', logisticType: 'remote' },
    ])
    expect(result.items[0].errorCode).toBe('MERCADOLIBRE_CATEGORY_MARKET_LOGISTICS_UNSUPPORTED')
    expect(result.items[0].nextAction).toBe('请选择共享类目')
    expect(result.items[0].platforms[0].errorCode).toBe('MERCADOLIBRE_CATEGORY_MARKET_LOGISTICS_UNSUPPORTED')
    expect(result.items[0].platforms[0].nextAction).toBe('请选择共享类目')
    expect(result.items[1].platforms[0].sitesToSell).toEqual([
      { siteId: 'MLA', logisticType: 'remote' },
    ])
  })
})
