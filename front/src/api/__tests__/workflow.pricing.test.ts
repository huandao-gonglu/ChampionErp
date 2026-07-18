import { describe, expect, it, vi } from 'vitest'
import { calculatePrice, generateCopy, imageTranslate, publishPrecheck } from '@/api/workflow'
import { apiClient } from '@/api/client'
import { createEmptyProduct } from '@/constants/initialState'
import { normalizeProductsIndex, toBackendProduct } from '@/api/workflow/normalizers'

vi.mock('@/api/client', () => ({
  API_REQUEST_TIMEOUT_MS: 30000,
  apiClient: {
    post: vi.fn(),
  },
}))

describe('calculatePrice API mapping', () => {
  it('posts pricing inputs and maps backend pricing fields for the UI', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: {
        ok: true,
        suggested_price_mxn: 739.83,
        suggested_price_usd: 43.52,
        wb_price_rub: 3011,
        shipping_cost_usd: 8,
        shipping_cost_cny: 58,
        total_cost_cny: 183,
        net_revenue_cny: 203.88,
        profit_cny: 78.88,
        profit_percent: 25,
        input: { usd_cny_rate: 7.25, mxn_usd_rate: 17, rub_cny_rate: 12 },
        exchange_rate_mode: 'manual',
        exchange_rates: {
          ok: true,
          source: 'manual',
          rates: { usd_cny_rate: 7.25, mxn_usd_rate: 17, rub_usd_rate: 73.5, rub_cny_rate: 12 },
        },
      },
    })

    const result = await calculatePrice({
      platform: 'mercadolibre',
      site: 'MLM',
      purchaseCostCny: 100,
      domesticFreightCny: 10,
      weightKg: 1.2,
      lengthCm: 30,
      widthCm: 20,
      heightCm: 15,
      commissionPercent: 15,
      targetMarginPercent: 25,
      usdCnyRate: 7.25,
      mxnUsdRate: 17,
      exchangeRateMode: 'manual',
      displayCurrencyMode: 'platform',
    })

    expect(apiClient.post).toHaveBeenCalledWith('/api/calculate-price', {
      platform: 'mercadolibre',
      site: 'MLM',
      purchase_cost: 100,
      domestic_freight: 10,
      weight_kg: 1.2,
      length_cm: 30,
      width_cm: 20,
      height_cm: 15,
      commission_percent: 15,
      target_margin_percent: 25,
      usd_cny_rate: 7.25,
      mxn_usd_rate: 17,
      exchange_rate_mode: 'manual',
      display_currency_mode: 'platform',
    })
    expect(result).toEqual({
      suggestedPriceMxn: 739.83,
      suggestedPriceUsd: 43.52,
      suggestedPriceCny: 315.52,
      wbPriceRub: 3011,
      shippingCostUsd: 8,
      shippingCostCny: 58,
      totalCostCny: 183,
      netRevenueCny: 203.88,
      profitCny: 78.88,
      marginPercent: 25,
      usdCnyRate: 7.25,
      mxnUsdRate: 17,
      rubUsdRate: 73.5,
      rubCnyRate: 12,
      exchangeRateMode: 'manual',
      exchangeRateSource: 'manual',
      exchangeRateFetchedAt: '',
      exchangeRateCached: false,
    })
  })
})

describe('generateCopy API mapping', () => {
  it('posts only product id and platform for single-product copy generation', async () => {
    const product = createEmptyProduct()
    product.productId = 'prod-1'
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: {
        ok: true,
        product: toBackendProduct(product),
        productsIndex: [],
      },
    })

    await generateCopy(product, 'ozon')

    expect(apiClient.post).toHaveBeenCalledWith('/api/generate-copy', {
      product_id: 'prod-1',
      platform: 'ozon',
    })
  })
})

describe('publishPrecheck API mapping', () => {
  it('maps draft statuses for the draft box', () => {
    const items = normalizeProductsIndex([
      {
        product_id: 'prod-1',
        title: 'Product 1',
        platforms: ['mercadolibre', 'ozon'],
        draft_statuses: {
          mercadolibre: 'claimed',
          wildberries: 'collected',
          ozon: 'ready_to_publish',
        },
      },
    ])

    expect(items[0].draftStatuses).toEqual({
      mercadolibre: 'claimed',
      wildberries: 'collected',
      ozon: 'ready_to_publish',
    })
  })

  it('keeps backend draft precheck metadata when posting a normalized product', () => {
    const product = createEmptyProduct()
    product.productId = 'prod-1'
    product.drafts.mercadolibre.title = 'Draft title'
    product.drafts.mercadolibre.stock = '10'
    product.drafts.mercadolibre.status = 'claimed'
    product.raw = {
      drafts: {
        mercadolibre: {
          publish_status: 'ready',
          validation_errors: [{ code: 'PRICING_NOT_APPLIED', severity: 'warning' }],
          pricing: { suggested_price: '172.68' },
        },
      },
      publish_preview: {
        mercadolibre: { ok: true },
      },
    }

    const result = toBackendProduct(product)
    const drafts = result.drafts as Record<string, Record<string, unknown>>

    expect(drafts.mercadolibre.publish_status).toBe('ready')
    expect(drafts.mercadolibre.validation_errors).toEqual([{ code: 'PRICING_NOT_APPLIED', severity: 'warning' }])
    expect(drafts.mercadolibre.pricing).toEqual({ suggested_price: '172.68' })
    expect(drafts.mercadolibre.status).toBe('claimed')
    expect(result.publish_preview).toEqual({ mercadolibre: { ok: true } })
  })

  it('keeps structured backend issues readable for the UI', async () => {
    const product = createEmptyProduct()
    product.productId = 'prod-1'
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: {
        ok: true,
        product: {
          product_id: 'prod-1',
          drafts: {
            mercadolibre: {
              enabled: true,
              attributes: {},
            },
          },
          source: {},
        },
        platforms: {
          mercadolibre: {
            ok: false,
            errors: [
              {
                code: 'PRICE_MISSING',
                field: 'price',
                message: '价格缺失或无效',
                severity: 'error',
                next_action: '前往核价页计算并应用售价',
              },
            ],
            warnings: [
              {
                code: 'CATEGORY_PATH_MISSING',
                field: 'category_path',
                message: '类目路径为空',
                severity: 'warning',
              },
            ],
            checked_at: '2026-06-02T00:00:00Z',
          },
        },
      },
    })

    const result = await publishPrecheck(product, ['mercadolibre'])

    expect(apiClient.post).toHaveBeenCalledWith('/api/publish-precheck', {
      product_id: 'prod-1',
      platforms: ['mercadolibre'],
    })
    expect(result.precheck.ok).toBe(false)
    expect(result.precheck.errors).toEqual(['price：价格缺失或无效（前往核价页计算并应用售价）'])
    expect(result.precheck.warnings).toEqual(['category_path：类目路径为空'])
    expect(result.precheck.errorItems[0]).toMatchObject({
      code: 'PRICE_MISSING',
      field: 'price',
      message: '价格缺失或无效',
      nextAction: '前往核价页计算并应用售价',
    })
  })
})

describe('imageTranslate API timeout', () => {
  it('scales the request timeout by selected image count', async () => {
    const product = createEmptyProduct()
    product.productId = 'prod-1'
    product.source.imagePool = [
      { id: 'img-1', url: '', path: '', previewUrl: '', origin: 'upload', usage: 'main', platforms: ['mercadolibre'], isMain: true, selected: true, status: 'ready', width: 1000, height: 1000 },
      { id: 'img-2', url: '', path: '', previewUrl: '', origin: 'upload', usage: 'detail', platforms: ['mercadolibre'], isMain: false, selected: true, status: 'ready', width: 1000, height: 1000 },
      { id: 'img-3', url: '', path: '', previewUrl: '', origin: 'upload', usage: 'detail', platforms: ['mercadolibre'], isMain: false, selected: false, status: 'ready', width: 1000, height: 1000 },
    ]
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: { ok: true, product: { source: { image_pool: [] } }, productsIndex: [] },
    })

    await imageTranslate(product, 'mercadolibre', 'Spanish (Mexico)')

    expect(apiClient.post).toHaveBeenCalledWith('/api/image-translate', expect.objectContaining({
      product_id: 'prod-1',
      image_ids: ['img-1', 'img-2'],
    }), { timeout: 60000 })
  })

  it('uses the whole image pool count when no images are selected', async () => {
    const product = createEmptyProduct()
    product.productId = 'prod-1'
    product.source.imagePool = [
      { id: 'img-1', url: '', path: '', previewUrl: '', origin: 'upload', usage: 'main', platforms: ['mercadolibre'], isMain: true, selected: false, status: 'ready', width: 1000, height: 1000 },
      { id: 'img-2', url: '', path: '', previewUrl: '', origin: 'upload', usage: 'detail', platforms: ['mercadolibre'], isMain: false, selected: false, status: 'ready', width: 1000, height: 1000 },
      { id: 'img-3', url: '', path: '', previewUrl: '', origin: 'upload', usage: 'detail', platforms: ['mercadolibre'], isMain: false, selected: false, status: 'ready', width: 1000, height: 1000 },
    ]
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: { ok: true, product: { source: { image_pool: [] } }, productsIndex: [] },
    })

    await imageTranslate(product, 'mercadolibre', 'Spanish (Mexico)')

    expect(apiClient.post).toHaveBeenCalledWith('/api/image-translate', expect.objectContaining({
      product_id: 'prod-1',
      image_ids: [],
    }), { timeout: 90000 })
  })
})
