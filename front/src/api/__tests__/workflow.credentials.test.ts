import { beforeEach, describe, expect, it, vi } from 'vitest'
import { apiClient } from '@/api/client'
import {
  collectProduct,
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
})
