// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import DraftEditorPanel from '@/components/domain/DraftEditorPanel.vue'
import type { DraftDetail, DraftProductContext, MarketplaceOption } from '@/types/workflow'

const platformOptions: MarketplaceOption[] = [{
  key: 'mercadolibre',
  label: '美客多',
  titleLimit: 60,
  sites: [
    { key: 'CBT', code: 'CBT', label: 'Global Selling', language: 'en-US' },
    { key: 'MLM', code: 'MLM', label: '墨西哥', language: 'es' },
    { key: 'MCO', code: 'MCO', label: '哥伦比亚', language: 'es' },
    { key: 'MLB', code: 'MLB', label: '巴西', language: 'pt-BR' },
  ],
}, {
  key: 'ozon',
  label: 'Ozon',
  sites: [{ key: 'global', code: 'global', label: '俄罗斯', language: 'ru-RU' }],
}]

const storeConfig = {
  mercadolibre: {
    account_site_id: 'CBT',
    listing_model: 'traditional_global_items',
    marketplace_bindings: [
      { seller_id: 'seller-mx-full', site_id: 'MLM', logistic_type: 'fulfillment', business_model: 'cross_border', pricing_model: 'price' },
      { seller_id: 'seller-mx', site_id: 'MLM', logistic_type: 'remote', business_model: 'cross_border', pricing_model: 'price' },
      { seller_id: 'seller-co', site_id: 'MCO', logistic_type: 'remote', business_model: 'cross_border', pricing_model: 'price' },
      { seller_id: 'seller-br', site_id: 'MLB', logistic_type: 'remote', business_model: 'cross_border', pricing_model: 'price' },
    ],
  },
}

function draft(): DraftDetail {
  return {
    draftId: 'draft-1',
    skuItems: [],
    grouping: { mode: 'combined', name: '' },
    productId: 'product-1',
    sourceProductId: 'product-1',
    platform: 'mercadolibre',
    platforms: ['mercadolibre'],
    targetSites: [{
      platform: 'mercadolibre',
      site: 'CBT',
      language: 'es',
      listingCurrency: 'USD',
      sitesToSell: [{ siteId: 'MLM', logisticType: 'remote' }],
    }],
    site: 'CBT',
    enabled: true,
    globalTitle: 'English title',
    title: 'Título',
    description: 'Descripción',
    brand: 'Brand',
    model: 'Model',
    bullets: ['Punto'],
    categoryId: '',
    descriptionCategoryId: '',
    categoryPath: '',
    attributes: {},
    pricing: {},
    images: [],
    status: 'claimed',
    language: 'es',
    stock: '',
    sku: '',
    upc: '',
    packageDimensions: { lengthCm: '', widthCm: '', heightCm: '', weightKg: '' },
    saleTerms: [],
    allowGtinExemption: false,
    validationErrors: [],
    publishStatus: '',
    lastPrecheck: {},
    lastPrecheckTarget: {},
    publication: null,
    createdAt: '',
    updatedAt: '',
    raw: {},
  }
}

const productContext: DraftProductContext = {
  skuItems: [],
  productId: 'product-1',
  sourceProductId: 'product-1',
  title: '商品',
  sourceTitle: '来源商品',
  sourcePlatform: '1688',
  sourceUrl: '',
  brand: '',
  model: '',
  sku: '',
  stock: '',
  cost: '',
  sourcePrice: '',
  currency: 'CNY',
  weightKg: '',
  dimensions: { lengthCm: '', widthCm: '', heightCm: '' },
  imagePool: [],
  raw: {},
}

describe('DraftEditorPanel', () => {
  it('保存本地草稿不受 Mercado 授权市场是否就绪影响', async () => {
    const wrapper = mount(DraftEditorPanel, {
      props: {
        draft: draft(),
        productContext,
        platformOptions,
        storeConfig: {
          mercadolibre: {
            account_site_id: 'CBT',
            listing_model: 'traditional_global_items',
            marketplace_bindings: [],
          },
        },
        loading: false,
      },
    })

    const saveButton = wrapper.get('button.btn-primary')
    expect(saveButton.text()).toBe('保存草稿')
    expect(saveButton.attributes('disabled')).toBeUndefined()

    await saveButton.trigger('click')
    expect(wrapper.emitted('save')).toHaveLength(1)
  })

  it('发布语言来自销售子市场，CBT 的 en-US 不出现在界面语言中', () => {
    const wrapper = mount(DraftEditorPanel, {
      props: { draft: draft(), productContext, platformOptions, storeConfig, loading: false, embedded: true },
    })

    expect(wrapper.get('[data-testid="draft-language-select"]').findAll('option').map((option) => option.attributes('value')))
      .toEqual(['', 'es', 'pt-BR', 'ru-RU'])
    expect(wrapper.get('[data-testid="draft-language-label"]').text()).toBe('发布语言')
    expect(wrapper.get('[data-testid="draft-title-label"]').text()).toBe('本地化平台标题')
    expect(wrapper.get('[data-testid="draft-global-title-input"]').attributes('maxlength')).toBe('60')
    expect(wrapper.get('[data-testid="draft-title-input"]').attributes('maxlength')).toBe('60')
  })

  it('User Products 只编辑 family_name，不显示传统根英文标题', () => {
    const wrapper = mount(DraftEditorPanel, {
      props: {
        draft: draft(),
        productContext,
        platformOptions,
        storeConfig: {
          mercadolibre: {
            account_site_id: 'CBT',
            listing_model: 'user_products',
            marketplace_bindings: [],
          },
        },
        loading: false,
        embedded: true,
      },
    })

    expect(wrapper.find('[data-testid="draft-global-title-input"]').exists()).toBe(false)
    expect(wrapper.get('[data-testid="draft-title-label"]').text()).toBe('产品族名称（family_name）')
  })

  it('listing_model 缺失时不展示无效的根英文标题输入', () => {
    const wrapper = mount(DraftEditorPanel, {
      props: {
        draft: draft(),
        productContext,
        platformOptions,
        storeConfig: { mercadolibre: {} },
        loading: false,
        embedded: true,
      },
    })

    expect(wrapper.find('[data-testid="draft-global-title-input"]').exists()).toBe(false)
  })

  it('本地化文案区不再重复承载市场、标题、售价和状态控件', () => {
    const wrapper = mount(DraftEditorPanel, {
      props: { draft: draft(), productContext, platformOptions, storeConfig, loading: false, embedded: true },
    })

    expect(wrapper.find('[data-testid="cbt-destination-selector"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="cbt-destination-localized-title"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="cbt-destination-price"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="cbt-destination-status"]').exists()).toBe(false)
  })

  it('现有市场选择器展示语言对应的子市场并映射为单一 CBT target', async () => {
    const currentDraft = draft()
    const wrapper = mount(DraftEditorPanel, {
      props: { draft: currentDraft, productContext, platformOptions, storeConfig, loading: false, embedded: true },
    })

    await wrapper.get('[data-testid="draft-market-select-button"]').trigger('click')
    expect(wrapper.text()).toContain('美客多 · 墨西哥（MLM）')
    expect(wrapper.text()).toContain('美客多 · 哥伦比亚（MCO）')
    expect(wrapper.text()).not.toContain('Global Selling（CBT）')
    expect(wrapper.text()).not.toContain('美客多 · 巴西（MLB）')

    await wrapper.get('[data-testid="draft-market-select-all"]').trigger('click')
    const operationRadios = wrapper.findAll('[data-testid="draft-market-operation-radio"]')
    expect(operationRadios).toHaveLength(2)
    await operationRadios[1].setValue()
    await wrapper.get('[data-testid="draft-market-select-save"]').trigger('click')

    const emittedTargets = wrapper.emitted('updateTargets')?.[0]?.[1]
    expect(emittedTargets).toEqual([{
      platform: 'mercadolibre',
      site: 'CBT',
      language: 'es',
      listingCurrency: 'USD',
      currencyFingerprint: undefined,
      sitesToSell: [
        { siteId: 'MLM', logisticType: 'fulfillment' },
        { siteId: 'MCO', logisticType: 'remote' },
      ],
    }])
  })

  it('同一市场无历史 operation 时优先 remote，已有选择则原样保留', async () => {
    const noHistory = draft()
    noHistory.targetSites[0]!.sitesToSell = []
    const firstWrapper = mount(DraftEditorPanel, {
      props: { draft: noHistory, productContext, platformOptions, storeConfig, loading: false, embedded: true },
    })
    await firstWrapper.get('[data-testid="draft-market-select-button"]').trigger('click')
    await firstWrapper.findAll('[data-testid="draft-target-checkbox"]')[0].setValue(true)
    await firstWrapper.get('[data-testid="draft-market-select-save"]').trigger('click')
    expect(firstWrapper.emitted('updateTargets')?.[0]?.[1]).toEqual([
      expect.objectContaining({
        site: 'CBT',
        sitesToSell: [{ siteId: 'MLM', logisticType: 'remote' }],
      }),
    ])

    const withHistory = draft()
    withHistory.targetSites[0]!.sitesToSell = [{ siteId: 'MLM', logisticType: 'fulfillment' }]
    const secondWrapper = mount(DraftEditorPanel, {
      props: { draft: withHistory, productContext, platformOptions, storeConfig, loading: false, embedded: true },
    })
    await secondWrapper.get('[data-testid="draft-market-select-button"]').trigger('click')
    await secondWrapper.get('[data-testid="draft-market-select-save"]').trigger('click')
    expect(secondWrapper.emitted('updateTargets')?.[0]?.[1]).toEqual([
      expect.objectContaining({
        site: 'CBT',
        sitesToSell: [{ siteId: 'MLM', logisticType: 'fulfillment' }],
      }),
    ])
  })

  it('清空市场只清空 CBT.sitesToSell，不删除内部 CBT target', async () => {
    const wrapper = mount(DraftEditorPanel, {
      props: { draft: draft(), productContext, platformOptions, storeConfig, loading: false, embedded: true },
    })
    await wrapper.get('[data-testid="draft-market-select-button"]').trigger('click')
    await wrapper.get('[data-testid="draft-market-select-clear"]').trigger('click')
    await wrapper.get('[data-testid="draft-market-select-save"]').trigger('click')

    expect(wrapper.emitted('updateTargets')?.[0]?.[1]).toEqual([
      expect.objectContaining({ platform: 'mercadolibre', site: 'CBT', language: 'es', sitesToSell: [] }),
    ])
  })
})
