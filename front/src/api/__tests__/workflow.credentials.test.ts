import { beforeEach, describe, expect, it, vi } from 'vitest'
import { apiClient } from '@/api/client'
import { collectBatch, collectProduct, saveCollectSettings } from '@/api/workflow/catalog'
import { toBackendProduct } from '@/api/workflow/normalizers'
import { createDefaultCollectForm, createEmptyProduct } from '@/constants/initialState'

vi.mock('@/api/client', () => ({
  API_REQUEST_TIMEOUT_MS: 30000,
  apiClient: {
    post: vi.fn(),
  },
}))

describe('采集请求边界', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('保存非敏感采集设置时不会提交空凭据占位', async () => {
    const form = createDefaultCollectForm()
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: { ok: true },
    })

    await saveCollectSettings(form)

    const payload = vi.mocked(apiClient.post).mock.calls[0]?.[1] as {
      appConfig: Record<string, unknown>
    }
    expect(payload.appConfig).not.toHaveProperty('alibaba_cookie')
    expect(payload).not.toHaveProperty('1688_api')
    expect((payload as { appConfig?: unknown }).appConfig || {}).not.toHaveProperty('1688_api')
  })

  it('单条和批量采集使用空平台值请求自动识别', async () => {
    const url = 'https://www.amazon.com/dp/ABC123'
    const form = createDefaultCollectForm()
    form.mode = 'browser'
    form.platform = 'unknown'
    form.productUrl = url
    form.productUrls = url
    vi.mocked(apiClient.post)
      .mockResolvedValueOnce({ data: { ok: true, product: toBackendProduct(createEmptyProduct()) } })
      .mockResolvedValueOnce({ data: { ok: true, items: [] } })

    await collectProduct(form)
    expect(apiClient.post).toHaveBeenLastCalledWith('/api/collect-source', {
      url,
      mode: 'browser',
      platform: '',
    })

    await collectBatch(form)
    expect(apiClient.post).toHaveBeenLastCalledWith('/api/collect-batch', expect.objectContaining({
      urls: url,
      mode: 'browser',
      platform: '',
    }))
    expect(vi.mocked(apiClient.post).mock.lastCall?.[1]).not.toHaveProperty('cookie')
  })
})
