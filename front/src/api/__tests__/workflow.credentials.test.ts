import { beforeEach, describe, expect, it, vi } from 'vitest'
import { apiClient } from '@/api/client'
import {
  collectProduct,
  collectBatch,
  collectFromBrowserTab,
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
    form.mode = 'api'
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: {
        ok: true,
        product: toBackendProduct(createEmptyProduct()),
        productsIndex: [],
      },
    })

    await collectProduct(form)

    const payload = vi.mocked(apiClient.post).mock.calls[0]?.[1] as Record<string, unknown>
    const apiConfig = payload['1688_api'] as Record<string, unknown>
    expect(payload).not.toHaveProperty('cookie')
    expect(apiConfig).not.toHaveProperty('app_key')
    expect(apiConfig).not.toHaveProperty('app_secret')
    expect(apiConfig).not.toHaveProperty('access_token')
  })

  it('只把显式请求瞬态 Cookie 发送一次', async () => {
    const form = createDefaultCollectForm()
    form.productUrl = 'https://detail.1688.com/offer/123456789.html'
    form.platform = '1688'
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: {
        ok: true,
        product: toBackendProduct(createEmptyProduct()),
        productsIndex: [],
      },
    })

    await collectProduct(form, {
      alibabaCookie: 'request-only-cookie-secret',
    })

    expect(vi.mocked(apiClient.post).mock.calls[0]?.[1]).toEqual(
      expect.objectContaining({
        cookie: 'request-only-cookie-secret',
      }),
    )
    expect(JSON.stringify(form)).not.toContain('request-only-cookie-secret')
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
    const apiConfig = payload.appConfig['1688_api'] as Record<string, unknown>
    expect(payload.appConfig).not.toHaveProperty('alibaba_cookie')
    expect(apiConfig).not.toHaveProperty('app_key')
    expect(apiConfig).not.toHaveProperty('app_secret')
    expect(apiConfig).not.toHaveProperty('access_token')
  })
  it('浏览器快照请求携带精确目标，响应不生成空商品', async () => {
    const form = createDefaultCollectForm()
    form.productUrl = 'https://detail.1688.com/offer/123.html'
    vi.mocked(apiClient.post).mockResolvedValueOnce({ data: {
      ok: true, saved_only: true, diagnostics: { html_snapshot_path: '/tmp/snapshot.html' },
      browserStatus: { connected: true, current_tabs: [] },
    } })
    const result = await collectFromBrowserTab(form, true, form.productUrl)
    expect(apiClient.post).toHaveBeenCalledWith('/api/collect-from-browser-tab', expect.objectContaining({
      tab_url: form.productUrl, product_url: form.productUrl, save_only: true,
    }))
    expect(result).toMatchObject({ savedOnly: true, diagnostics: { html_snapshot_path: '/tmp/snapshot.html' } })
    expect(result).not.toHaveProperty('product')
  })

  it('自动识别不把 unknown 当作明确平台发送，批量凭据只来自本次参数', async () => {
    const form = createDefaultCollectForm()
    form.platform = 'unknown'
    form.productUrl = 'https://amazon.com/dp/ABC123'
    form.productUrls = form.productUrl
    vi.mocked(apiClient.post).mockResolvedValueOnce({ data: { ok: true, product: toBackendProduct(createEmptyProduct()) } })
    await collectProduct(form)
    expect(apiClient.post).toHaveBeenLastCalledWith('/api/collect-source', expect.objectContaining({ platform: '' }))
    vi.mocked(apiClient.post).mockResolvedValueOnce({ data: { ok: true, items: [] } })
    await collectBatch(form, { alibabaCookie: 'transient-cookie' })
    expect(apiClient.post).toHaveBeenLastCalledWith('/api/collect-batch', expect.objectContaining({ platform: '', urls: form.productUrl, cookie: 'transient-cookie' }))
    expect(JSON.stringify(form)).not.toContain('transient-cookie')
  })

  it('等待验证响应不生成商品，继续请求只携带原标签身份', async () => {
    const form = { ...createDefaultCollectForm(), productUrl: 'https://detail.1688.com/offer/123.html' }
    const waiting = { ok: false, status: 'waiting_verification', verification: { browser_tab_id: 'original', source_url: form.productUrl, platform: '1688' }, diagnostics: {} }
    vi.mocked(apiClient.post).mockResolvedValue({ data: waiting })
    const result = await collectProduct(form)
    expect(result).not.toHaveProperty('product')
    expect(result.verification?.browserTabId).toBe('original')
    expect((await collectFromBrowserTab(form, false, '', 'original')).savedOnly).toBe(false)
    expect(apiClient.post).toHaveBeenLastCalledWith('/api/collect-from-browser-tab', expect.objectContaining({ browser_tab_id: 'original', product_url: form.productUrl }))
    const signal = new AbortController().signal
    vi.mocked(apiClient.post).mockResolvedValueOnce({ data: { ok: true, status: 'ready', message: '验证已完成' } })
    await inspectCollectionVerification(result.verification!, signal)
    expect(apiClient.post).toHaveBeenLastCalledWith('/api/collect-verification', { browser_tab_id: 'original', source_url: form.productUrl }, { signal })
  })

})
