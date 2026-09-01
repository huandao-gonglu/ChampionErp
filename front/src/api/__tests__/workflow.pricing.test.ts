import { describe, expect, it, vi } from 'vitest'
import { calculatePrice, generateCopy, imageEdit, imageTranslate, publishPrecheck } from '@/api/workflow'
import { apiClient } from '@/api/client'
import { createEmptyDraftDetail, createEmptyProduct } from '@/constants/initialState'
import { PRODUCT_SCHEMA_VERSION, normalizeBackendProduct, normalizeProductsIndex, normalizePublishPrecheck, toBackendProduct } from '@/api/workflow/normalizers'

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
        results: [{
          target_key: 'mercadolibre:mlm',
          platform: 'mercadolibre',
          site: 'MLM',
          listing_currency: 'MXN',
          currency_fingerprint: 'sha256:store-fingerprint',
          suggested_price: { amount: '739.83', currency: 'MXN' },
          applied_price: { amount: '739.83', currency: 'MXN' },
          minimum_price: { amount: '600.00', currency: 'MXN' },
          converted_prices: { USD: '43.52', CNY: '315.52' },
          calculation_basis: { listing_currency: 'MXN' },
          calculation_fingerprint: 'fingerprint',
          shipping_cost_usd: 8,
          shipping_cost_cny: 58,
          total_cost_cny: 183,
          net_revenue_cny: 203.88,
          profit_cny: 78.88,
          margin_percent: 25,
          commission_percent: 15,
          pricing_mode: 'margin',
          target_margin_percent: 25,
          markup_percent: 30,
          shipping_quote_mode: 'manual',
          shipping_currency: 'USD',
          shipping_amount: 8,
          usd_cny_rate: 7.25,
          mxn_usd_rate: 17,
          rub_cny_rate: 12,
          errors: [],
        }],
        input: { common: { usd_cny_rate: 7.25, mxn_usd_rate: 17, rub_cny_rate: 12 } },
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
      packagingCostCny: 0,
      otherCostCny: 0,
      weightKg: 1.2,
      lengthCm: 30,
      widthCm: 20,
      heightCm: 15,
      usdCnyRate: 7.25,
      mxnUsdRate: 17,
      rubCnyRate: 12,
      exchangeRateMode: 'manual',
      targets: [
        {
          targetKey: 'mercadolibre:mlm',
          platform: 'mercadolibre',
          site: 'MLM',
          sitesToSell: [],
          listingCurrency: 'MXN',
          commissionPercent: 15,
          paymentFeePercent: 0,
          otherFeePercent: 0,
          pricingMode: 'margin',
          targetMarginPercent: 25,
          markupPercent: 30,
          shippingQuoteMode: 'manual',
          shippingCurrency: 'USD',
          shippingAmount: 8,
          manualPrice: null,
        },
      ],
    })

    expect(apiClient.post).toHaveBeenCalledWith('/api/calculate-price', {
      platform: 'mercadolibre',
      site: 'MLM',
      common: {
        purchase_cost: 100,
        domestic_freight: 10,
        packaging_cost: 0,
        other_cost: 0,
        weight_kg: 1.2,
        length_cm: 30,
        width_cm: 20,
        height_cm: 15,
        usd_cny_rate: 7.25,
        mxn_usd_rate: 17,
        rub_cny_rate: 12,
        exchange_rate_mode: 'manual',
      },
      purchase_cost: 100,
      domestic_freight: 10,
      packaging_cost: 0,
      other_cost: 0,
      weight_kg: 1.2,
      length_cm: 30,
      width_cm: 20,
      height_cm: 15,
      usd_cny_rate: 7.25,
      mxn_usd_rate: 17,
      rub_cny_rate: 12,
      exchange_rate_mode: 'manual',
      targets: [
        {
          target_key: 'mercadolibre:mlm',
          platform: 'mercadolibre',
          site: 'MLM',
          sites_to_sell: [],
          listing_currency: 'MXN',
          commission_percent: 15,
          payment_fee_percent: 0,
          other_fee_percent: 0,
          pricing_mode: 'margin',
          target_margin_percent: 25,
          markup_percent: 30,
          shipping_quote_mode: 'manual',
          shipping_currency: 'USD',
          shipping_amount: 8,
          manual_price: null,
        },
      ],
    })
    expect(result).toEqual({
      results: [
        {
          targetKey: 'mercadolibre:mlm',
          platform: 'mercadolibre',
          site: 'MLM',
          listingCurrency: 'MXN',
          currencyFingerprint: 'sha256:store-fingerprint',
          suggestedPrice: { amount: '739.83', currency: 'MXN' },
          appliedPrice: { amount: '739.83', currency: 'MXN' },
          appliedNetProceeds: null,
          destinationResults: [],
          convertedPrices: { USD: '43.52', CNY: '315.52' },
          calculationBasis: { listing_currency: 'MXN' },
          calculationFingerprint: 'fingerprint',
          shippingCostUsd: 8,
          shippingCostCny: 58,
          totalCostCny: 183,
          netRevenueCny: 203.88,
          profitCny: 78.88,
          marginPercent: 25,
          commissionPercent: 15,
          paymentFeePercent: 0,
          otherFeePercent: 0,
          pricingMode: 'margin',
          targetMarginPercent: 25,
          markupPercent: 30,
          shippingQuoteMode: 'manual',
          shippingCurrency: 'USD',
          shippingAmount: 8,
          shippingSource: '',
          commissionCny: 0,
          paymentFeeCny: 0,
          otherFeeCny: 0,
          minimumPrice: { amount: '600.00', currency: 'MXN' },
          billableWeightKg: 0,
          usdCnyRate: 7.25,
          mxnUsdRate: 17,
          rubCnyRate: 12,
          isLoss: false,
          errors: [],
          raw: expect.any(Object),
        },
      ],
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

  it('sends CBT sales destinations in pricing targets', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: {
        ok: true,
        results: [{
          target_key: 'mercadolibre:cbt',
          platform: 'mercadolibre',
          site: 'CBT',
          listing_currency: 'USD',
          applied_price: { amount: '29.90', currency: 'USD' },
          applied_net_proceeds: { amount: '24.50', currency: 'USD' },
          destination_results: [{
            site_id: 'MLM',
            logistic_type: 'remote',
            pricing_model: 'net_proceeds',
            price: null,
            net_proceeds: { amount: '24.50', currency: 'USD' },
            calculation_fingerprint: 'destination-fingerprint',
          }, {
            site_id: 'MLB',
            logistic_type: 'remote',
            pricing_model: 'unknown',
            price: { amount: '29.90', currency: 'USD' },
            net_proceeds: null,
          }],
          errors: [],
        }],
        input: { common: {} },
        exchange_rates: { rates: {} },
      },
    })

    const result = await calculatePrice({
      platform: 'mercadolibre',
      site: 'CBT',
      purchaseCostCny: 100,
      domesticFreightCny: 0,
      packagingCostCny: 0,
      otherCostCny: 0,
      weightKg: 1,
      lengthCm: 10,
      widthCm: 10,
      heightCm: 10,
      usdCnyRate: 0,
      mxnUsdRate: 0,
      rubCnyRate: 0,
      exchangeRateMode: 'live',
      targets: [{
        targetKey: 'mercadolibre:cbt',
        platform: 'mercadolibre',
        site: 'CBT',
        sitesToSell: [
          {
            siteId: 'MLM',
            logisticType: 'remote',
            price: '29.90',
            listingTypeId: 'gold_special',
            freeShipping: true,
            saleTerms: [{ id: 'WARRANTY_TYPE', value_name: 'Sin garantía' }],
            netProceeds: '24.50',
          },
          { siteId: 'MLB', logisticType: 'fulfillment' },
        ],
        listingCurrency: 'USD',
        commissionPercent: 16,
        paymentFeePercent: 0,
        otherFeePercent: 0,
        pricingMode: 'margin',
        targetMarginPercent: 30,
        markupPercent: 30,
        shippingQuoteMode: 'auto',
        shippingCurrency: 'USD',
        shippingAmount: 0,
        manualPrice: null,
      }],
    })

    expect(apiClient.post).toHaveBeenCalledWith('/api/calculate-price', expect.objectContaining({
      targets: [expect.objectContaining({
        target_key: 'mercadolibre:cbt',
        sites_to_sell: [
          {
            site_id: 'MLM',
            logistic_type: 'remote',
            price: '29.90',
            listing_type_id: 'gold_special',
            free_shipping: true,
            sale_terms: [{ id: 'WARRANTY_TYPE', value_name: 'Sin garantía' }],
            net_proceeds: '24.50',
          },
          { site_id: 'MLB', logistic_type: 'fulfillment' },
        ],
      })],
    }))
    expect(result.results[0]).toEqual(expect.objectContaining({
      appliedPrice: { amount: '29.90', currency: 'USD' },
      appliedNetProceeds: { amount: '24.50', currency: 'USD' },
      destinationResults: [{
        siteId: 'MLM',
        logisticType: 'remote',
        pricingModel: 'net_proceeds',
        price: null,
        netProceeds: { amount: '24.50', currency: 'USD' },
        calculationFingerprint: 'destination-fingerprint',
      }],
    }))
  })
})

describe('generateCopy API mapping', () => {
  it('posts product id and platform for single-product copy generation', async () => {
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

  it('binds copy generation to the current draft and language', async () => {
    const product = createEmptyProduct()
    product.productId = 'prod-1'
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: {
        ok: true,
        product: toBackendProduct(product),
        productsIndex: [],
      },
    })

    await generateCopy(product, 'ozon', {
      draftId: 'draft-1',
      language: 'ru-RU',
      mode: 'rewrite',
    })

    expect(apiClient.post).toHaveBeenCalledWith('/api/generate-copy', {
      product_id: 'prod-1',
      platform: 'ozon',
      draft_id: 'draft-1',
      language: 'ru-RU',
      mode: 'rewrite',
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
          yandex: 'collected',
          ozon: 'ready_to_publish',
        },
      },
    ])

    expect(items[0].draftStatuses).toEqual({
      mercadolibre: 'claimed',
      yandex: 'collected',
      ozon: 'ready_to_publish',
    })
  })

  it('keeps product metadata but does not post platform drafts with a normalized product', () => {
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
      future_only_field: '不得透传',
      source: {
        variants: [{ sku: 'variant-1' }],
        future_source_field: '不得透传',
      },
    }

    const result = toBackendProduct(product)

    expect(result.drafts).toBeUndefined()
    expect(result.publish_preview).toEqual({ mercadolibre: { ok: true } })
    expect(result).not.toHaveProperty('future_only_field')
    expect(result.source?.variants).toEqual([{ sku: 'variant-1' }])
    expect(result.source).not.toHaveProperty('future_source_field')
  })

  it('writes product descriptions and selling points into source data', () => {
    const product = createEmptyProduct()
    product.source.description = '可折叠收纳盒，适用于厨房和衣柜。'
    product.sellingPoints = ['可折叠收纳', '节省空间']

    const result = toBackendProduct(product)

    expect(result.source).toEqual(expect.objectContaining({
      description: '可折叠收纳盒，适用于厨房和衣柜。',
      bullets: ['可折叠收纳', '节省空间'],
    }))
    expect(result.schema_version).toBe(PRODUCT_SCHEMA_VERSION)
    expect(result).not.toHaveProperty('id')
    expect(result).not.toHaveProperty('source_url')
  })

  it('only reads canonical fields from a versioned product', () => {
    const product = normalizeBackendProduct({
      schema_version: PRODUCT_SCHEMA_VERSION,
      product_id: 'canonical-id',
      name: '规范名称',
      source: {
        source_url: 'https://canonical.example/item',
        source_platform: '1688',
        title: '规范标题',
        image_pool: [],
      },
      drafts: {},
    })

    expect(product.productId).toBe('canonical-id')
    expect(product.name).toBe('规范名称')
    expect(product.source.sourceUrl).toBe('https://canonical.example/item')
  })

  it('rejects products without the current schema version', () => {
    expect(() => normalizeBackendProduct({
      id: 'legacy-id',
      title: '旧商品',
      sourceImages: ['legacy.jpg'],
    })).toThrow('不支持的商品数据 schema_version：未声明')

    expect(() => normalizeBackendProduct({
      schema_version: 0,
      product_id: 'old-version',
    })).toThrow(`当前仅接受 ${PRODUCT_SCHEMA_VERSION}`)

    expect(() => normalizeBackendProduct({
      schema_version: PRODUCT_SCHEMA_VERSION,
      product_id: 'legacy-fields',
      title: '旧商品',
      source: {},
    })).toThrow('商品数据包含已移除的旧字段：title')
  })

  it('keeps structured backend issues readable for the UI', async () => {
    const draft = createEmptyDraftDetail('mercadolibre')
    draft.draftId = 'draft-1'
    draft.targetSites = [{
      platform: 'mercadolibre',
      site: 'CBT',
      language: 'en-US',
      listingCurrency: 'USD',
      sitesToSell: [
        { siteId: 'MLA', logisticType: 'remote' },
        { siteId: 'MLU', logisticType: 'remote' },
      ],
    }]
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: {
        ok: true,
        draft: {
          draft_id: 'draft-1',
          platform: 'mercadolibre',
          site: 'CBT',
          target_sites: [{ platform: 'mercadolibre', site: 'CBT', language: 'en-US', currency: 'USD' }],
          enabled: true,
          attributes: {},
        },
        productContext: { product_id: 'prod-1' },
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
            parent: {
              ok: true,
              status: 'passed',
              errors: [],
              warnings: [],
            },
            markets: [
              {
                site_id: 'MLA',
                logistic_type: 'remote',
                ok: false,
                status: 'blocked',
                errors: [{ code: 'MARKET_PRICE_INVALID', field: 'price', message: '阿根廷售价无效' }],
                warnings: [],
              },
              {
                site_id: 'MLU',
                logistic_type: 'remote',
                ok: true,
                status: 'passed',
                errors: [],
                warnings: [{ code: 'SHIPPING_NOTICE', field: 'shipping', message: '发布后检查平台运费结果' }],
              },
            ],
            checked_at: '2026-06-02T00:00:00Z',
          },
        },
      },
    })

    const result = await publishPrecheck(draft, draft.targetSites[0])

    expect(apiClient.post).toHaveBeenCalledWith('/api/publish-precheck', {
      draft_id: 'draft-1',
      platform: 'mercadolibre',
      site: 'CBT',
    })
    expect(result.precheck.ok).toBe(false)
    expect(result.precheck.errors).toEqual(['价格缺失或无效（前往核价页计算并应用售价）'])
    expect(result.precheck.warnings).toEqual(['类目路径为空'])
    expect(result.precheck.errorItems[0]).toMatchObject({
      code: 'PRICE_MISSING',
      field: 'price',
      message: '价格缺失或无效',
      nextAction: '前往核价页计算并应用售价',
    })
    expect(result.precheck.parent).toEqual({
      ok: true,
      status: 'passed',
      errors: [],
      warnings: [],
    })
    expect(result.precheck.marketChecks).toEqual([
      expect.objectContaining({
        siteId: 'MLA',
        logisticType: 'remote',
        status: 'blocked',
        errors: [expect.objectContaining({ code: 'MARKET_PRICE_INVALID', message: '阿根廷售价无效' })],
      }),
      expect.objectContaining({
        siteId: 'MLU',
        logisticType: 'remote',
        status: 'passed',
        warnings: [expect.objectContaining({ code: 'SHIPPING_NOTICE', message: '发布后检查平台运费结果' })],
      }),
    ])
  })

  it('任一分层 scope 阻断时即使顶层 ok=true 也失败关闭', async () => {
    const draft = createEmptyDraftDetail('mercadolibre')
    draft.draftId = 'draft-layered-fail-closed'
    draft.targetSites = [{
      platform: 'mercadolibre',
      site: 'CBT',
      language: 'en-US',
      listingCurrency: 'USD',
      sitesToSell: [
        { siteId: 'MLA', logisticType: 'remote' },
        { siteId: 'MLC', logisticType: 'remote' },
      ],
    }]
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: {
        ok: true,
        draft: {
          draft_id: draft.draftId,
          platform: 'mercadolibre',
          site: 'CBT',
          target_sites: [{ platform: 'mercadolibre', site: 'CBT', language: 'en-US', currency: 'USD' }],
          enabled: true,
          attributes: {},
        },
        productContext: { product_id: 'prod-layered-fail-closed' },
        platforms: {
          mercadolibre: {
            ok: true,
            errors: [],
            warnings: [],
            parent: {
              ok: true,
              status: 'passed',
              errors: [],
              warnings: [],
            },
            markets: [
              {
                site_id: 'MLA',
                logistic_type: 'remote',
                ok: true,
                status: 'blocked',
                errors: [],
                warnings: [],
              },
              {
                site_id: 'MLC',
                logistic_type: 'remote',
                ok: true,
                status: 'passed',
                errors: [{ code: 'MARKET_LIMIT', field: 'shipping', message: '智利物流限制不通过' }],
                warnings: [],
              },
            ],
          },
        },
      },
    })

    const result = await publishPrecheck(draft, draft.targetSites[0])

    expect(result.precheck.ok).toBe(false)
    expect(result.precheck.marketChecks).toEqual([
      expect.objectContaining({ siteId: 'MLA', status: 'blocked', ok: false }),
      expect.objectContaining({
        siteId: 'MLC',
        status: 'blocked',
        ok: false,
        errors: [expect.objectContaining({ code: 'MARKET_LIMIT' })],
      }),
    ])
  })

  it('Mercado 返回旧式顶层 ok 但缺少分层 scope 时要求重新预检', async () => {
    const draft = createEmptyDraftDetail('mercadolibre')
    draft.draftId = 'draft-old-flat-precheck'
    draft.targetSites = [{
      platform: 'mercadolibre',
      site: 'CBT',
      language: 'en-US',
      listingCurrency: 'USD',
      sitesToSell: [{ siteId: 'MLA', logisticType: 'remote' }],
    }]
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: {
        ok: true,
        draft: {
          draft_id: draft.draftId,
          platform: 'mercadolibre',
          site: 'CBT',
          target_sites: [{ platform: 'mercadolibre', site: 'CBT', language: 'en-US', currency: 'USD' }],
          enabled: true,
          attributes: {},
        },
        productContext: { product_id: 'prod-old-flat-precheck' },
        platforms: {
          mercadolibre: {
            ok: true,
            errors: [],
            warnings: [],
            checked_at: '2026-08-30T00:00:00Z',
          },
        },
      },
    })

    const result = await publishPrecheck(draft, draft.targetSites[0])

    expect(result.precheck.ok).toBe(false)
    expect(result.precheck.errorItems).toEqual([
      expect.objectContaining({
        code: 'LAYERED_PRECHECK_REQUIRED',
        nextAction: '重新执行上架预检',
      }),
    ])
  })

  it.each([
    ['未知状态', 'unverified'],
    ['缺失状态', ''],
  ])('scope %s 时失败关闭，不把 ok=true 伪装成通过', (_label, status) => {
    const market = {
      site_id: 'MCO',
      logistic_type: 'remote',
      ok: true,
      errors: [],
      warnings: [],
      ...(status ? { status } : {}),
    }
    const result = normalizePublishPrecheck({
      ok: true,
      errors: [],
      warnings: [],
      parent: { ok: true, status: 'passed', errors: [], warnings: [] },
      markets: [market],
    }, {
      requireLayeredScopes: true,
      expectedMarkets: [{ siteId: 'MCO', logisticType: 'remote' }],
    })

    expect(result.ok).toBe(false)
    expect(result.marketChecks).toEqual([
      expect.objectContaining({ siteId: 'MCO', ok: false, status: 'blocked' }),
    ])
    expect(result.errorItems).toEqual([
      expect.objectContaining({ code: 'PRECHECK_RESULT_INCONSISTENT' }),
    ])
  })

  it('市场 scope 与当前选择顺序无关但集合必须完全一致', () => {
    const result = normalizePublishPrecheck({
      ok: true,
      errors: [],
      warnings: [],
      parent: { ok: true, status: 'passed', errors: [], warnings: [] },
      markets: [
        { site_id: 'MLC', logistic_type: 'remote', ok: true, status: 'passed', errors: [], warnings: [] },
        { site_id: 'MLA', logistic_type: 'remote', ok: true, status: 'passed', errors: [], warnings: [] },
      ],
    }, {
      requireLayeredScopes: true,
      expectedMarkets: [
        { siteId: 'MLA', logisticType: 'remote' },
        { siteId: 'MLC', logisticType: 'remote' },
      ],
    })

    expect(result.ok).toBe(true)
    expect(result.errorItems).toEqual([])
  })

  it.each([
    [
      '缺少市场',
      [{ siteId: 'MLA', logisticType: 'remote' }, { siteId: 'MLC', logisticType: 'remote' }],
      [{ site_id: 'MLA', logistic_type: 'remote' }],
    ],
    [
      '重复市场',
      [{ siteId: 'MLA', logisticType: 'remote' }, { siteId: 'MLC', logisticType: 'remote' }],
      [{ site_id: 'MLA', logistic_type: 'remote' }, { site_id: 'MLA', logistic_type: 'remote' }],
    ],
    [
      '多出市场',
      [{ siteId: 'MLA', logisticType: 'remote' }],
      [{ site_id: 'MLA', logistic_type: 'remote' }, { site_id: 'MLC', logistic_type: 'remote' }],
    ],
  ])('%s时阻断 Mercado 分层预检', (_label, expectedMarkets, markets) => {
    const result = normalizePublishPrecheck({
      ok: true,
      errors: [],
      warnings: [],
      parent: { ok: true, status: 'passed', errors: [], warnings: [] },
      markets: markets.map((market) => ({
        ...market,
        ok: true,
        status: 'passed',
        errors: [],
        warnings: [],
      })),
    }, {
      requireLayeredScopes: true,
      expectedMarkets,
    })

    expect(result.ok).toBe(false)
    expect(result.errorItems).toEqual([
      expect.objectContaining({ code: 'LAYERED_PRECHECK_MARKETS_MISMATCH' }),
    ])
  })

  it.each([
    { label: '顶层 ok=false 但没有阻断明细', topState: { ok: false } },
    { label: '顶层 status 与 ok 冲突', topState: { ok: true, status: 'blocked' } },
  ])('$label 时合成状态不一致错误', ({ topState }) => {
    const result = normalizePublishPrecheck({
      ...topState,
      errors: [],
      warnings: [],
      parent: { ok: true, status: 'passed', errors: [], warnings: [] },
      markets: [
        { site_id: 'MLA', logistic_type: 'remote', ok: true, status: 'passed', errors: [], warnings: [] },
      ],
    }, {
      requireLayeredScopes: true,
      expectedMarkets: [{ siteId: 'MLA', logisticType: 'remote' }],
    })

    expect(result.ok).toBe(false)
    expect(result.errorItems).toEqual([
      expect.objectContaining({ code: 'PRECHECK_RESULT_INCONSISTENT' }),
    ])
  })

  it('缺少用户文案时保留结构化 code 和 field，但摘要不暴露它们', () => {
    const result = normalizePublishPrecheck({
      ok: false,
      errors: [{ code: 'RAW_INTERNAL_CODE', field: 'sites_to_sell[0].category_id' }],
      warnings: [],
      parent: { ok: true, status: 'passed', errors: [], warnings: [] },
      markets: [
        { site_id: 'MLA', logistic_type: 'remote', ok: true, status: 'passed', errors: [], warnings: [] },
      ],
    }, {
      requireLayeredScopes: true,
      expectedMarkets: [{ siteId: 'MLA', logisticType: 'remote' }],
    })

    expect(result.errorItems).toEqual([
      expect.objectContaining({
        code: 'RAW_INTERNAL_CODE',
        field: 'sites_to_sell[0].category_id',
        message: '预检返回了未说明原因的阻断',
      }),
    ])
    expect(result.errors).toEqual(['预检返回了未说明原因的阻断'])
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
      data: { ok: true, product: toBackendProduct(createEmptyProduct()), productsIndex: [] },
    })

    await imageTranslate(product, 'mercadolibre', 'Spanish (Mexico)')

    expect(apiClient.post).toHaveBeenCalledWith('/api/image-translate', expect.objectContaining({
      product_id: 'prod-1',
      source_image_ids: ['img-1', 'img-2'],
    }), { timeout: 60000 })
  })

  it('rejects the request when no images are selected', async () => {
    vi.mocked(apiClient.post).mockClear()
    const product = createEmptyProduct()
    product.productId = 'prod-1'
    product.source.imagePool = [
      { id: 'img-1', url: '', path: '', previewUrl: '', origin: 'upload', usage: 'main', platforms: ['mercadolibre'], isMain: true, selected: false, status: 'ready', width: 1000, height: 1000 },
      { id: 'img-2', url: '', path: '', previewUrl: '', origin: 'upload', usage: 'detail', platforms: ['mercadolibre'], isMain: false, selected: false, status: 'ready', width: 1000, height: 1000 },
      { id: 'img-3', url: '', path: '', previewUrl: '', origin: 'upload', usage: 'detail', platforms: ['mercadolibre'], isMain: false, selected: false, status: 'ready', width: 1000, height: 1000 },
    ]
    await expect(imageTranslate(product, 'mercadolibre', 'Spanish (Mexico)'))
      .rejects.toThrow('请先勾选要翻译/重绘的图片')
    expect(apiClient.post).not.toHaveBeenCalled()
  })
})

describe('imageEdit API payload', () => {
  it('posts selected source images and the user prompt to image-edit', async () => {
    const product = createEmptyProduct()
    product.productId = 'prod-1'
    product.source.imagePool = [
      { id: 'img-1', url: '', path: '', previewUrl: '', origin: 'upload', usage: 'main', platforms: ['mercadolibre'], isMain: true, selected: true, status: 'ready', width: 1000, height: 1000 },
      { id: 'img-2', url: '', path: '', previewUrl: '', origin: 'upload', usage: 'detail', platforms: ['mercadolibre'], isMain: false, selected: false, status: 'ready', width: 1000, height: 1000 },
    ]
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: { ok: true, product: toBackendProduct(createEmptyProduct()), productsIndex: [] },
    })

    await imageEdit(product, 'mercadolibre', '  扣除背景，保留产品主体  ', {
      draftId: 'draft-1',
      applyToDraft: true,
      draftImageStrategy: 'append',
      sourceImageIds: ['img-2'],
    })

    expect(apiClient.post).toHaveBeenCalledWith('/api/image-edit', expect.objectContaining({
      product_id: 'prod-1',
      platform: 'mercadolibre',
      prompt: '扣除背景，保留产品主体',
      draft_id: 'draft-1',
      apply_to_draft: true,
      draft_image_strategy: 'append',
      source_image_ids: ['img-2'],
    }), { timeout: 30000 })
  })
})
