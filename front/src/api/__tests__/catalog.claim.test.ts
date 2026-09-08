import { beforeEach, describe, expect, it, vi } from 'vitest'
import { apiClient } from '@/api/client'
import { claimProducts } from '@/api/workflow/catalog'

vi.mock('@/api/client', () => ({ API_REQUEST_TIMEOUT_MS: 30000, apiClient: { post: vi.fn() } }))

describe('商品库推到草稿请求', () => {
  beforeEach(() => vi.clearAllMocks())

  it('一次提交商品和选中的市场，由后端按语言分组并返回实际数量', async () => {
    vi.mocked(apiClient.post).mockResolvedValue({ data: {
      ok: true, claimed_count: 1, draft_count: 2,
      items: [{ product_id: 'p1', ok: true }, { product_id: 'missing', ok: false, error: '商品不存在' }],
      productsIndex: [], draftsIndex: [],
    } })
    const result = await claimProducts(['p1', 'missing'], [
      { platform: 'mercadolibre', site: 'MLM', language: 'es', listingCurrency: '' },
      { platform: 'mercadolibre', site: 'MLC', language: 'es', listingCurrency: '' },
      { platform: 'ozon', site: 'global', language: 'ru-RU', listingCurrency: '' },
    ])
    expect(apiClient.post).toHaveBeenCalledOnce()
    expect(apiClient.post).toHaveBeenCalledWith('/api/claim-products', {
      product_ids: ['p1', 'missing'],
      targets: [
        { platform: 'mercadolibre', site: 'MLM', language: 'es' },
        { platform: 'mercadolibre', site: 'MLC', language: 'es' },
        { platform: 'ozon', site: 'global', language: 'ru-RU' },
      ],
    })
    expect(result).toEqual({
      claimedCount: 1, draftCount: 2, failures: [{ productId: 'missing', error: '商品不存在' }],
      productsIndex: [], draftsIndex: [],
    })
  })

  it('全部失败时不能显示推送成功', async () => {
    vi.mocked(apiClient.post).mockResolvedValue({ data: {
      ok: true, claimed_count: 0, draft_count: 0,
      items: [{ product_id: 'missing', ok: false, error: '商品不存在' }],
    } })
    await expect(claimProducts(['missing'], [{ platform: 'ozon', site: 'global', language: 'ru-RU', listingCurrency: '' }])).rejects.toThrow('商品不存在')
  })
})
