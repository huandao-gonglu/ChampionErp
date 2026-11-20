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
      { aiConversationId: 'presentation-a' },
    )
    expect(result.ok).toBe(true)
  })

  it('浏览器模型测试允许用户完成交互登录后继续原测试', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({ data: { ok: true }, status: 200 })
    const model = { id: 'canvas', connection_type: 'browser' }
    await testAiModel(model, 'canvas-login')
    expect(apiClient.post).toHaveBeenCalledWith('/api/test-ai-model', { model }, {
      aiConversationId: 'canvas-login', timeout: 600_000,
    })
  })
})

it('桌面先保存本机连接，业务请求只发送引用和指纹', async () => {
  const configureModels = vi.fn().mockResolvedValue({ fingerprints: { canvas: 'actual-config' }, models: {} })
  vi.stubGlobal('championDesktop', { localOperations: true, configureModels })
  vi.mocked(apiClient.post).mockResolvedValue({ data: { ok: true }, status: 200 })
  await testAiModel({ id: 'canvas', connection_type: 'browser', browser_profile: 'personal', browser_proxy: 'http://127.0.0.1:7890' })
  expect(configureModels).toHaveBeenCalledWith({ canvas: { browser_profile: 'personal', browser_proxy: 'http://127.0.0.1:7890' } })
  expect(apiClient.post).toHaveBeenLastCalledWith('/api/test-ai-model', {
    model: { id: 'canvas', connection_type: 'browser', local_connection_id: 'canvas', local_config_fingerprint: 'actual-config' },
  }, { aiConversationId: undefined, timeout: 600_000 })
  vi.unstubAllGlobals()
})
