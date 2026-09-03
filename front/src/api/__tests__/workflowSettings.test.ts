import { beforeEach, describe, expect, it, vi } from 'vitest'
import { apiClient } from '@/api/client'
import { testAiModel } from '@/api/workflow/settings'

vi.mock('@/api/client', () => ({
  API_REQUEST_TIMEOUT_MS: 30_000,
  apiClient: {
    post: vi.fn(),
    get: vi.fn(),
  },
}))

describe('workflow settings API', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('使用 transport header 关联模型能力测试与 AI Work presentation', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: {
        ok: true,
        message: '测试通过',
        channel: 'ai_model',
        model_id: 'model-a',
      },
      status: 200,
    })
    const model = { id: 'model-a', probe_only_capability: 'chat' }

    const result = await testAiModel(model, 'presentation-a')

    expect(apiClient.post).toHaveBeenCalledWith(
      '/api/test-ai-model',
      { model },
      { aiPresentationId: 'presentation-a' },
    )
    expect(result.ok).toBe(true)
  })
})
