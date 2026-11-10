import { beforeEach, describe, expect, it, vi } from 'vitest'
import { apiClient } from '@/api/client'
import {
  collectProduct,
  collectBatch,
  resumeCollection,
  inspectCollectionVerification,
  saveCollectSettings,
} from '@/api/workflow/catalog'
import {
  createDefaultCollectForm,
  createEmptyProduct,
} from '@/constants/initialState'
import { toBackendProduct } from '@/api/workflow/normalizers'

vi.mock('@/api/client', () => ({
  API_REQUEST_TIMEOUT_MS: 30000,
  apiClient: {
    post: vi.fn(),
  },
}))

describe('采集请求凭据边界', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('默认采集请求不从表单重放 Cookie 或 1688 凭据', async () => {
    const form = createDefaultCollectForm()
    form.productUrl = 'https://detail.1688.com/offer/123456789.html'
    form.platform = '1688'
    form.mode = 'browser'
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: {
        ok: true,
        product: toBackendProduct(createEmptyProduct()),
        productsIndex: [],
      },
    })

    await collectProduct(form)

    const payload = vi.mocked(apiClient.post).mock.calls[0]?.[1] as Record<string, unknown>
    expect(payload).not.toHaveProperty('cookie')
    expect(payload).not.toHaveProperty('1688_api')
    expect((payload as { appConfig?: unknown }).appConfig || {}).not.toHaveProperty('1688_api')
  })

  it('API 模式直接提交 1688 链接，使用服务端授权', async () => {
    const form = createDefaultCollectForm()
    form.mode = 'api'
    form.productUrl = 'https://detail.1688.com/offer/123456789.html?spm=test'
    vi.mocked(apiClient.post).mockResolvedValueOnce({ data: { ok: true, product: toBackendProduct(createEmptyProduct()) } })
    await collectProduct(form)
    expect(apiClient.post).toHaveBeenCalledWith('/api/collect-1688', { mode: 'api', url: form.productUrl })
    form.productUrl = 'https://1688.com.example.org/offer/123456789.html'
    await expect(collectProduct(form)).rejects.toThrow('请输入 1688 商品详情链接')
    expect(apiClient.post).toHaveBeenCalledTimes(1)
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


  it('自动识别不把 unknown 当作明确平台发送', async () => {
    const form = createDefaultCollectForm()
    form.platform = 'unknown'
    form.productUrl = 'https://amazon.com/dp/ABC123'
    form.productUrls = form.productUrl
    vi.mocked(apiClient.post).mockResolvedValueOnce({ data: { ok: true, product: toBackendProduct(createEmptyProduct()) } })
    await collectProduct(form)
    expect(apiClient.post).toHaveBeenLastCalledWith('/api/collect-source', expect.objectContaining({ platform: '' }))
    vi.mocked(apiClient.post).mockResolvedValueOnce({ data: { ok: true, items: [] } })
    await collectBatch(form)
    expect(apiClient.post).toHaveBeenLastCalledWith('/api/collect-batch', expect.objectContaining({ platform: '', urls: form.productUrl }))
    expect(vi.mocked(apiClient.post).mock.lastCall?.[1]).not.toHaveProperty('cookie')
  })

  it('等待验证响应不生成商品，继续请求只携带原标签身份', async () => {
    const form = { ...createDefaultCollectForm(), productUrl: 'https://detail.1688.com/offer/123.html' }
    const waiting = { ok: false, status: 'waiting_verification', verification: { browser_tab_id: 'original', source_url: form.productUrl, platform: '1688' }, diagnostics: {} }
    vi.mocked(apiClient.post).mockResolvedValue({ data: waiting })
    const result = await collectProduct(form)
    expect(result).not.toHaveProperty('product')
    expect(result.verification?.browserTabId).toBe('original')
    expect(await resumeCollection(result.verification!)).not.toHaveProperty('product')
    expect(apiClient.post).toHaveBeenLastCalledWith('/api/collect-resume', expect.objectContaining({ browser_tab_id: 'original', source_url: form.productUrl }))
    const signal = new AbortController().signal
    vi.mocked(apiClient.post).mockResolvedValueOnce({ data: { ok: true, status: 'ready', message: '验证已完成' } })
    await inspectCollectionVerification(result.verification!, signal)
    expect(apiClient.post).toHaveBeenLastCalledWith('/api/collect-verification', { browser_tab_id: 'original', source_url: form.productUrl }, { signal })
  })

})
