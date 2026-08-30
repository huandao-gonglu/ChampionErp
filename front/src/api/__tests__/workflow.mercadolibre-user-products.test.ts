import { beforeEach, describe, expect, it, vi } from 'vitest'
import { apiClient } from '@/api/client'
import { fetchMercadoLibreUserProducts, pauseMercadoLibreUserProduct, reconcilePublishJob } from '@/api/workflow/publishing'

vi.mock('@/api/client', () => ({
  API_REQUEST_TIMEOUT_MS: 30_000,
  apiClient: {
    get: vi.fn(),
    post: vi.fn(),
  },
}))

describe('Mercado Libre User Products API', () => {
  beforeEach(() => vi.clearAllMocks())

  it('从新端点读取 Siteless User Product 与市场投影', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: {
        ok: true,
        items: [{
          product_id: 'product-1',
          draft_id: 'draft-1',
          title: '测试商品',
          thumbnail: '/image.jpg',
          model: 'user_products',
          account_user_id: 'account-user-1',
          parent_item_id: 'CBT-PARENT-1',
          parent_user_product_id: 'UP-PARENT-1',
          siteless_user_product_id: 'UP-SITELESS-1',
          siteless_family_id: 'FAMILY-1',
          seller_id: 'seller-global',
          family_name: '测试 Family',
          status: 'partial',
          markets: [{
            site_id: 'MLM',
            item_id: 'MLM-ITEM-1',
            user_product_id: 'UP-MLM-1',
            seller_id: 'seller-mx',
            logistic_type: 'remote',
            status: 'active',
            price: 399,
            currency_id: 'MXN',
            listing_type_id: 'gold_special',
            error: '',
            updated_at: '2026-08-26T09:00:00Z',
          }],
          updated_at: '2026-08-26T10:00:00Z',
        }],
        pagination: { page: 2, per_page: 25, total: 31, total_pages: 2 },
        refresh_errors: [{ site_id: 'MLB', message: 'MLB 同步暂不可用' }],
        refresh_scope: 'identity_mapping_only',
        checked_at: '2026-08-26T10:01:00Z',
      },
    })

    const result = await fetchMercadoLibreUserProducts('all', 2, 25, true)

    expect(apiClient.get).toHaveBeenCalledWith('/api/mercadolibre/user-products?status=all&page=2&per_page=25&refresh=true')
    expect(result.items[0]).toEqual(expect.objectContaining({
      productId: 'product-1',
      draftId: 'draft-1',
      sitelessUserProductId: 'UP-SITELESS-1',
      accountUserId: 'account-user-1',
      markets: [expect.objectContaining({ siteId: 'MLM', itemId: 'MLM-ITEM-1', userProductId: 'UP-MLM-1' })],
    }))
    expect(result.refreshErrors).toEqual([{ site_id: 'MLB', message: 'MLB 同步暂不可用' }])
    expect(result.refreshScope).toBe('identity_mapping_only')
    expect(result.checkedAt).toBe('2026-08-26T10:01:00Z')
  })

  it('默认只读取本地 publication 快照，不触发远端 identity mapping 对账', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: {
        ok: true,
        items: [],
        pagination: { page: 1, per_page: 50, total: 0, total_pages: 1 },
        refresh_errors: [],
        refresh_scope: 'local_snapshot',
        checked_at: '2026-08-26T10:02:00Z',
      },
    })

    const result = await fetchMercadoLibreUserProducts()

    expect(apiClient.get).toHaveBeenCalledWith('/api/mercadolibre/user-products?status=active&page=1&per_page=50&refresh=false')
    expect(result.refreshScope).toBe('local_snapshot')
  })

  it('按 Siteless ID 暂停整个 User Product', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: { ok: true, siteless_user_product_id: 'UP-SITELESS-1', status: 'paused' },
    })

    await pauseMercadoLibreUserProduct('UP-SITELESS-1')

    expect(apiClient.post).toHaveBeenCalledWith('/api/mercadolibre/pause-user-product', {
      siteless_user_product_id: 'UP-SITELESS-1',
    })
  })

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
