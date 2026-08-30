import { describe, expect, it } from 'vitest'
import {
  normalizeDraft,
  normalizeDraftsIndex,
  normalizeMarketplaceOptions,
  normalizeProductsIndex,
  normalizePublishLogs,
  normalizeTargetSites,
  toBackendDraft,
  toBackendTargetSite,
} from '@/api/workflow/normalizers'
import { draftTargetLabel, draftTargetsForLanguage } from '@/utils/draftTargetOptions'

describe('workflow 当前 wire schema', () => {
  it('索引只读取当前 snake_case 字段', () => {
    const [product] = normalizeProductsIndex([{
      product_id: 'product-current',
      productId: 'product-legacy',
      id: 'product-legacy-id',
      title: '当前标题',
      name: '旧标题',
      main_image: '/current.jpg',
      mainImage: '/legacy.jpg',
      source_platform: '1688',
      sourcePlatform: 'legacy',
      workflow_status: 'images_ready',
      workflowStatus: 'published',
      draft_statuses: { mercadolibre: 'images_ready' },
      draftStatuses: { mercadolibre: 'published' },
    }])
    const [draft] = normalizeDraftsIndex([{
      draft_id: 'draft-current',
      draftId: 'draft-legacy',
      product_id: 'product-current',
      productId: 'product-legacy',
      source_product_id: 'source-current',
      sourceProductId: 'source-legacy',
      platform: 'mercadolibre',
      platforms: ['mercadolibre'],
      site: 'MLM',
      target_sites: [{
        platform: 'mercadolibre',
        site: 'MLM',
        language: 'es-MX',
        currency: 'MXN',
        category_id: 'MLM-CURRENT',
      }],
      targetSites: [{
        platform: 'mercadolibre',
        site: 'MLM',
        category_id: 'MLM-LEGACY',
      }],
      product_title: '当前商品标题',
      productTitle: '旧商品标题',
      main_image: '/draft-current.jpg',
      mainImage: '/draft-legacy.jpg',
    }])

    expect(product).toEqual(expect.objectContaining({
      productId: 'product-current',
      title: '当前标题',
      mainImage: '/current.jpg',
      sourcePlatform: '1688',
      workflowStatus: 'images_ready',
      draftStatuses: { mercadolibre: 'images_ready' },
    }))
    expect(draft).toEqual(expect.objectContaining({
      draftId: 'draft-current',
      productId: 'product-current',
      sourceProductId: 'source-current',
      productTitle: '当前商品标题',
      mainImage: '/draft-current.jpg',
    }))
    expect(draft?.targetSites[0]?.categoryId).toBe('MLM-CURRENT')
  })

  it('发布日志保留当前日志变体，但不再读取 camelCase 别名', () => {
    const [log] = normalizePublishLogs([{
      job_id: 'job-current',
      jobId: 'job-legacy',
      product_id: 'product-current',
      productId: 'product-legacy',
      platform: 'mercadolibre',
      started_at: '',
      time: '2026-07-30 10:00:00',
      error_message: '',
      error: '当前日志错误详情',
      request_payload_path: '/current/request.json',
      requestPayloadPath: '/legacy/request.json',
    }])

    expect(log).toEqual(expect.objectContaining({
      jobId: 'job-current',
      productId: 'product-current',
      startedAt: '2026-07-30 10:00:00',
      errorMessage: '当前日志错误详情',
      requestPayloadPath: '/current/request.json',
    }))
  })

  it('CBT 销售子市场使用 sites_to_sell 双向转换且不会保留 CBT 目的地', () => {
    const [target] = normalizeTargetSites([{
      platform: 'mercadolibre',
      site: 'CBT',
      language: 'en-US',
      listing_currency: 'USD',
      sites_to_sell: [
        {
          site_id: 'MLM',
          logistic_type: 'remote',
          price: '29.90',
          listing_type_id: 'gold_special',
          status: 'paused',
          free_shipping: false,
          sale_terms: [{ id: 'WARRANTY_TYPE', value_name: 'Sin garantía' }],
          net_proceeds: '24.50',
        },
        { site_id: 'MLM', logistic_type: 'fulfillment', price: '99.00' },
        { site_id: 'CBT', logistic_type: 'remote' },
        { site_id: 'MLB', logistic_type: 'fulfillment' },
      ],
    }], 'mercadolibre', 'CBT', 'en-US')

    expect(target?.sitesToSell).toEqual([
      {
        siteId: 'MLM',
        logisticType: 'remote',
        price: '29.90',
        listingTypeId: 'gold_special',
        status: 'paused',
        freeShipping: false,
        saleTerms: [{ id: 'WARRANTY_TYPE', value_name: 'Sin garantía' }],
        netProceeds: '24.50',
      },
      { siteId: 'MLB', logisticType: 'fulfillment' },
    ])
    expect(toBackendTargetSite(target!)).toMatchObject({
      sites_to_sell: [
        {
          site_id: 'MLM',
          logistic_type: 'remote',
          price: '29.90',
          listing_type_id: 'gold_special',
          status: 'paused',
          free_shipping: false,
          sale_terms: [{ id: 'WARRANTY_TYPE', value_name: 'Sin garantía' }],
          net_proceeds: '24.50',
        },
        { site_id: 'MLB', logistic_type: 'fulfillment' },
      ],
    })

    const [oldTarget] = normalizeTargetSites([{
      platform: 'mercadolibre',
      site: 'CBT',
      language: 'en-US',
      listing_currency: 'USD',
    }], 'mercadolibre', 'CBT', 'en-US')
    expect(oldTarget?.sitesToSell).toEqual([])
  })

  it('Mercado publication 在草稿 snake_case 与 camelCase 之间完整往返', () => {
    const draft = normalizeDraft({
      draft_id: 'draft-1',
      platforms: ['mercadolibre'],
      site: 'CBT',
      language: 'en-US',
      publication: {
        model: 'user_products',
        account_user_id: 'account-user-1',
        siteless_user_product_id: 'UP-SITELESS-1',
        siteless_family_id: 'FAMILY-1',
        parent_item_id: 'CBT-PARENT-1',
        parent_user_product_id: 'UP-PARENT-1',
        seller_id: 'seller-global',
        status: 'partial',
        family_name: '测试商品',
        markets: [{
          site_id: 'MLM',
          item_id: 'MLM-ITEM-1',
          user_product_id: 'UP-MLM-1',
          seller_id: 'seller-mx',
          logistic_type: 'remote',
          status: 'active',
          price: 399.5,
          net_proceeds: '23.40',
          free_shipping: false,
          sale_terms: [{ id: 'WARRANTY_TYPE', value_name: 'Sin garantía' }],
          currency_id: 'MXN',
          listing_type_id: 'gold_special',
          error: [{ code: 'market-warning' }],
          last_operation: { action: 'update', status: 'partially_applied' },
          updated_at: '2026-08-26T00:00:00Z',
        }],
        confirmed_payload: { family_name: '测试商品', price: '29.90' },
        error: { code: 'publication-warning' },
        last_operation: { action: 'add_markets', status: 'partially_applied' },
        updated_at: '2026-08-26T00:00:00Z',
      },
    }, 'en-US')

    expect(draft.publication).toEqual({
      model: 'user_products',
      accountUserId: 'account-user-1',
      sitelessUserProductId: 'UP-SITELESS-1',
      sitelessFamilyId: 'FAMILY-1',
      parentItemId: 'CBT-PARENT-1',
      parentUserProductId: 'UP-PARENT-1',
      sellerId: 'seller-global',
      status: 'partial',
      familyName: '测试商品',
      markets: [{
        siteId: 'MLM',
        itemId: 'MLM-ITEM-1',
        userProductId: 'UP-MLM-1',
        sellerId: 'seller-mx',
        logisticType: 'remote',
        status: 'active',
        price: 399.5,
        netProceeds: '23.40',
        freeShipping: false,
        saleTerms: [{ id: 'WARRANTY_TYPE', value_name: 'Sin garantía' }],
        currencyId: 'MXN',
        listingTypeId: 'gold_special',
        error: [{ code: 'market-warning' }],
        lastOperation: { action: 'update', status: 'partially_applied' },
        updatedAt: '2026-08-26T00:00:00Z',
      }],
      confirmedPayload: { family_name: '测试商品', price: '29.90' },
      error: { code: 'publication-warning' },
      lastOperation: { action: 'add_markets', status: 'partially_applied' },
      updatedAt: '2026-08-26T00:00:00Z',
    })
    expect(toBackendDraft(draft).publication).toEqual({
      model: 'user_products',
      account_user_id: 'account-user-1',
      siteless_user_product_id: 'UP-SITELESS-1',
      siteless_family_id: 'FAMILY-1',
      parent_item_id: 'CBT-PARENT-1',
      parent_user_product_id: 'UP-PARENT-1',
      seller_id: 'seller-global',
      status: 'partial',
      family_name: '测试商品',
      markets: [{
        site_id: 'MLM',
        item_id: 'MLM-ITEM-1',
        user_product_id: 'UP-MLM-1',
        seller_id: 'seller-mx',
        logistic_type: 'remote',
        status: 'active',
        price: 399.5,
        net_proceeds: '23.40',
        free_shipping: false,
        sale_terms: [{ id: 'WARRANTY_TYPE', value_name: 'Sin garantía' }],
        currency_id: 'MXN',
        listing_type_id: 'gold_special',
        error: [{ code: 'market-warning' }],
        last_operation: { action: 'update', status: 'partially_applied' },
        updated_at: '2026-08-26T00:00:00Z',
      }],
      confirmed_payload: { family_name: '测试商品', price: '29.90' },
      error: { code: 'publication-warning' },
      last_operation: { action: 'add_markets', status: 'partially_applied' },
      updated_at: '2026-08-26T00:00:00Z',
    })
  })

  it('草稿品牌和型号作为根字段双向转换', () => {
    const draft = normalizeDraft({
      draft_id: 'draft-brand-model',
      brand: 'Root Brand',
      model: 'Root Model',
      attributes: {
        BRAND: '旧重复品牌',
        MODEL: '旧重复型号',
      },
    }, 'en-US')

    expect(draft.brand).toBe('Root Brand')
    expect(draft.model).toBe('Root Model')
    expect(toBackendDraft(draft)).toMatchObject({
      brand: 'Root Brand',
      model: 'Root Model',
    })
  })

  it('显式 traditional_global_items publication 只有 parent_item_id 也会保留', () => {
    const draft = normalizeDraft({
      draft_id: 'draft-traditional',
      platforms: ['mercadolibre'],
      publication: {
        model: 'traditional_global_items',
        parent_item_id: 'CBT-TRADITIONAL-1',
      },
    }, 'en-US')

    expect(draft.publication).toMatchObject({
      model: 'traditional_global_items',
      parentItemId: 'CBT-TRADITIONAL-1',
      sitelessUserProductId: '',
      markets: [],
    })
    expect(toBackendDraft(draft).publication).toMatchObject({
      model: 'traditional_global_items',
      parent_item_id: 'CBT-TRADITIONAL-1',
      siteless_user_product_id: '',
      markets: [],
    })
  })

  it.each([
    ['缺失 model', { parent_item_id: 'CBT-UNMODELED-1' }],
    ['未知 model', { model: 'legacy_or_future_model', siteless_user_product_id: 'UP-UNKNOWN-1' }],
  ])('%s 即使含远端身份也不作隐式兼容', (_label, publication) => {
    const draft = normalizeDraft({
      draft_id: 'draft-invalid-model',
      platforms: ['mercadolibre'],
      publication,
    }, 'en-US')

    expect(draft.publication).toBeNull()
  })

  it('CBT 仅作为内部父目标，语言和市场选项来自 Mercado 销售子市场', () => {
    const options = normalizeMarketplaceOptions([{
      key: 'mercadolibre',
      label: '美客多',
      title_limit: 60,
      sites: [
        { key: 'CBT', code: 'CBT', label: '全局', language: 'es' },
        { key: 'MLM', code: 'MLM', label: '墨西哥', language: 'es' },
      ],
    }])

    expect(options[0]?.titleLimit).toBe(60)

    expect(draftTargetsForLanguage(options, 'pt-BR')).toEqual([])
    expect(draftTargetsForLanguage(options, 'es')).toEqual([{
      platform: 'mercadolibre',
      site: 'MLM',
      language: 'es',
      listingCurrency: '',
    }])
    expect(draftTargetLabel(options, {
      platform: 'mercadolibre',
      site: 'MLM',
      language: 'es',
      listingCurrency: '',
    })).toBe('美客多 · 墨西哥（MLM）')
  })
})
