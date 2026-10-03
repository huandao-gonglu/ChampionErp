import { describe, expect, it } from 'vitest'
import {
  normalizeDraft,
  normalizeImageAsset,
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
  it('图片读取商品资产及图片池展示接口的像素尺寸', () => {
    for (const dimensions of [{ width: 800, height: 600 }, { width_px: 800, height_px: 600 }]) {
      expect(normalizeImageAsset({ id: 'sku-red', ...dimensions })).toMatchObject({ id: 'sku-red', width: 800, height: 600 })
    }
  })

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
    }], 'mercadolibre', 'CBT')

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
    }], 'mercadolibre', 'CBT')
    expect(oldTarget?.sitesToSell).toEqual([])
  })

  it('多目标草稿的首个目标缺字段时不会继承根编辑态', () => {
    const draft = normalizeDraft({
      platforms: ['yandex', 'ozon'],
      site: 'global',
      language: 'es-MX',
      category_id: 'ROOT-CATEGORY',
      description_category_id: 'ROOT-DESCRIPTION-CATEGORY',
      category_path: 'Root / Category',
      attributes: { ROOT_ATTRIBUTE: 'root value' },
      validation_errors: ['Root validation error'],
      target_sites: [{
        platform: 'ozon',
        site: 'global',
        language: 'ru-RU',
        listing_currency: 'RUB',
      }, {
        platform: 'yandex',
        site: 'global',
        language: 'ru-RU',
        listing_currency: 'RUB',
        category_id: '60996608',
        category_path: 'Yandex / Бытовая техника',
        attributes: { YANDEX_BRAND: 'Yandex brand' },
        validation_errors: ['Yandex validation error'],
      }],
    }, 'ru-RU')

    expect(draft.categoryId).toBe('ROOT-CATEGORY')
    expect(draft.targetSites[0]).toEqual(expect.objectContaining({
      platform: 'ozon',
      categoryId: '',
      descriptionCategoryId: '',
      categoryPath: '',
      attributes: {},
      validationErrors: [],
    }))
    expect(draft.targetSites[1]).toEqual(expect.objectContaining({
      platform: 'yandex',
      categoryId: '60996608',
      categoryPath: 'Yandex / Бытовая техника',
      attributes: { YANDEX_BRAND: 'Yandex brand' },
      validationErrors: ['Yandex validation error'],
    }))
  })

  it('target_sites 整体缺失时合成的目标不会迁移根刊登字段', () => {
    const draft = normalizeDraft({
      platforms: ['yandex'],
      site: 'global',
      language: 'es-MX',
      category_id: '60996608',
      description_category_id: '17028674',
      category_path: 'Yandex / Бытовая техника',
      attributes: { YANDEX_BRAND: 'Yandex brand' },
      validation_errors: ['Yandex validation error'],
      publish_status: 'ready',
      status: 'ready_to_publish',
    }, 'ru-RU')

    expect(draft.categoryId).toBe('60996608')
    expect(draft.targetSites[0]).toEqual(expect.objectContaining({
      platform: 'yandex',
      site: 'global',
      language: '',
      categoryId: '',
      descriptionCategoryId: '',
      categoryPath: '',
      attributes: {},
      validationErrors: [],
      publishStatus: '',
      status: '',
    }))
  })

  it('单目标缺失刊登字段时也不会迁移根编辑态', () => {
    const draft = normalizeDraft({
      platforms: ['yandex'],
      site: 'global',
      language: 'ru-RU',
      category_id: '60996608',
      category_path: 'Yandex / Бытовая техника',
      attributes: { YANDEX_BRAND: 'Yandex brand' },
      target_sites: [{
        platform: 'yandex',
        site: 'global',
        listing_currency: 'RUB',
      }],
    }, 'ru-RU')

    expect(draft.categoryId).toBe('60996608')
    expect(draft.targetSites[0]).toEqual(expect.objectContaining({
      platform: 'yandex',
      language: '',
      categoryId: '',
      descriptionCategoryId: '',
      categoryPath: '',
      attributes: {},
      validationErrors: [],
    }))
  })

  it('草稿不再读取或写出根级销售事实与远端身份', () => {
    const draft = normalizeDraft({ stock: '10', sku: 'old', upc: 'old', package_dimensions: { weight_kg: '9' }, publication: { model: 'user_products', siteless_user_product_id: 'old' } }, 'en-US')
    for (const key of ['stock', 'sku', 'upc', 'packageDimensions', 'publication']) expect(draft).not.toHaveProperty(key)
    for (const key of ['stock', 'sku', 'upc', 'package_dimensions', 'publication']) expect(toBackendDraft(draft)).not.toHaveProperty(key)
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
