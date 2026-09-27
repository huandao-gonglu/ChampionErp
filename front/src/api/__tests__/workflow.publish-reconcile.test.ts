import { beforeEach, describe, expect, it, vi } from 'vitest'
import { apiClient } from '@/api/client'
import { reconcilePublishJob } from '@/api/workflow/publishing'

vi.mock('@/api/client', () => ({
  API_REQUEST_TIMEOUT_MS: 30_000,
  apiClient: {
    get: vi.fn(),
    post: vi.fn(),
  },
}))

describe('发布任务对账 API', () => {
  beforeEach(() => vi.clearAllMocks())

  it('只读对账结果未知的发布任务', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: { ok: true, resolution: 'applied', resolved: true },
    })

    const result = await reconcilePublishJob('job-unknown', 'mercadolibre')

    expect(apiClient.post).toHaveBeenCalledWith('/api/publish-bus/reconcile', {
      job_id: 'job-unknown',
      platform: 'mercadolibre',
    })
    expect(result.resolution).toBe('applied')
  })
})
