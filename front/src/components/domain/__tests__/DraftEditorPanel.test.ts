// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import DraftEditorPanel from '@/components/domain/DraftEditorPanel.vue'
import type { DraftDetail, DraftProductContext, MarketplaceOption, MarketplaceSiteToSell, MarketplaceTargetSite } from '@/types/workflow'

const platformOptions: MarketplaceOption[] = [
  {
    key: 'mercadolibre',
    label: '美客多',
    sites: [
      { key: 'CBT', code: 'CBT', label: '全局', language: 'en-US' },
      { key: 'MLM', code: 'MLM', label: '墨西哥', language: 'es' },
      { key: 'MCO', code: 'MCO', label: '哥伦比亚', language: 'es' },
      { key: 'MLB', code: 'MLB', label: '巴西', language: 'pt-BR' },
    ],
  },
  {
    key: 'ozon',
    label: 'Ozon',
    sites: [
      { key: 'global', code: 'global', label: '俄罗斯', language: 'ru-RU' },
    ],
  },
]

function draft(): DraftDetail {
  return {
    draftId: 'draft-1',
    productId: 'product-1',
    sourceProductId: 'product-1',
    platform: 'mercadolibre',
    platforms: ['mercadolibre'],
    targetSites: [{
      platform: 'mercadolibre',
      site: 'MLM',
      language: 'es',
      listingCurrency: 'MXN',
      categoryId: 'MLM-123',
    }],
    site: 'MLM',
    enabled: true,
    title: '标题',
    description: '描述',
    bullets: ['卖点'],
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
    createdAt: '',
    updatedAt: '',
    raw: {},
  }
}

const productContext: DraftProductContext = {
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

const globalSellingStoreConfig = {
  mercadolibre: {
    account_site_id: 'CBT',
    listing_currency: 'USD',
    currency_source: 'global_selling_contract',
    marketplace_bindings: [
      { seller_id: 'seller-mx', site_id: 'MLM', logistic_type: 'remote', business_model: 'cross_border' },
      { seller_id: 'seller-br', site_id: 'MLB', logistic_type: 'fulfillment', business_model: 'cross_border' },
      { seller_id: 'seller-global', site_id: 'CBT', logistic_type: 'remote' },
    ],
  },
}

function cbtDraft(): DraftDetail {
  const currentDraft = draft()
  currentDraft.site = 'CBT'
  currentDraft.language = 'en-US'
  currentDraft.targetSites = [{
    platform: 'mercadolibre',
    site: 'CBT',
    language: 'en-US',
    listingCurrency: 'USD',
  }]
  return currentDraft
}

function applySitesToSell(_draft: DraftDetail, target: MarketplaceTargetSite, sitesToSell: MarketplaceSiteToSell[]) {
  target.sitesToSell = sitesToSell
}

describe('DraftEditorPanel', () => {
  it('允许在草稿工作台切换发布语言', async () => {
    const currentDraft = draft()
    const wrapper = mount(DraftEditorPanel, {
      props: {
        draft: currentDraft,
        productContext,
        platformOptions,
        loading: false,
        embedded: true,
      },
    })

    const languageSelect = wrapper.get('[data-testid="draft-language-select"]')
    expect(languageSelect.findAll('option').map((option) => option.attributes('value'))).toEqual(['', 'en-US', 'es', 'pt-BR', 'ru-RU'])

    await languageSelect.setValue('ru-RU')

    expect(wrapper.emitted('updateLanguage')).toEqual([[currentDraft, 'ru-RU']])
  })

  it('复用市场下拉复选器并只展示同语言市场', async () => {
    const currentDraft = draft()
    const wrapper = mount(DraftEditorPanel, {
      props: {
        draft: currentDraft,
        productContext,
        platformOptions,
        loading: false,
        embedded: true,
      },
    })

    await wrapper.get('[data-testid="draft-market-select-button"]').trigger('click')

    expect(wrapper.text()).toContain('美客多 · 墨西哥（MLM）')
    expect(wrapper.text()).toContain('美客多 · 哥伦比亚（MCO）')
    expect(wrapper.text()).not.toContain('美客多 · 全局（CBT）')
    expect(wrapper.text()).not.toContain('美客多 · 巴西（MLB）')
    expect(wrapper.text()).not.toContain('Ozon · 俄罗斯（global）')

    const targetCheckboxes = wrapper.findAll('[data-testid="draft-target-checkbox"]')
    expect(targetCheckboxes).toHaveLength(2)
    expect((targetCheckboxes[0].element as HTMLInputElement).checked).toBe(true)
    expect((targetCheckboxes[1].element as HTMLInputElement).checked).toBe(false)

    await targetCheckboxes[1].setValue(true)
    await wrapper.get('[data-testid="draft-market-select-save"]').trigger('click')

    expect(wrapper.emitted('updateTargets')?.[0]?.[0]).toEqual(currentDraft)
    // 新增市场目标不再从站点 option 携带币种：发布币种由店铺授权配置在
    // 核价时写入；已有目标保留其币种快照。
    expect(wrapper.emitted('updateTargets')?.[0]?.[1]).toEqual([
      expect.objectContaining({ platform: 'mercadolibre', site: 'MLM', language: 'es', listingCurrency: 'MXN' }),
      expect.objectContaining({ platform: 'mercadolibre', site: 'MCO', language: 'es', listingCurrency: '' }),
    ])
  })

  it('CBT 草稿只展示授权同步的子市场，旧草稿不会自动全选', async () => {
    const currentDraft = cbtDraft()
    const wrapper = mount(DraftEditorPanel, {
      props: {
        draft: currentDraft,
        productContext,
        platformOptions,
        storeConfig: globalSellingStoreConfig,
        loading: false,
        embedded: false,
        onUpdateSitesToSell: applySitesToSell,
      },
    })

    expect(currentDraft.targetSites[0]?.sitesToSell).toBeUndefined()
    expect(wrapper.get('[data-testid="cbt-destination-error"]').text()).toContain('至少选择一个')
    expect(wrapper.findAll('[data-testid="cbt-destination-option"]')).toHaveLength(2)
    expect(wrapper.text()).toContain('墨西哥（MLM）')
    expect(wrapper.text()).toContain('巴西（MLB）')
    expect(wrapper.text()).not.toContain('seller-global')
    expect(wrapper.get('button.btn-primary').attributes('disabled')).toBeDefined()

    const checkboxes = wrapper.findAll('[data-testid="cbt-destination-checkbox"]')
    expect(checkboxes.every((checkbox) => !(checkbox.element as HTMLInputElement).checked)).toBe(true)
    await checkboxes[0].setValue(true)

    expect(wrapper.emitted('updateSitesToSell')?.[0]?.[2]).toEqual([{ siteId: 'MLM', logisticType: 'remote' }])
    expect(currentDraft.targetSites[0]?.sitesToSell).toEqual([{ siteId: 'MLM', logisticType: 'remote' }])
    expect(wrapper.find('[data-testid="cbt-destination-error"]').exists()).toBe(false)
    expect(wrapper.get('button.btn-primary').attributes('disabled')).toBeUndefined()
  })

  it('区域站点草稿不显示 Global Selling 销售国家选择器', () => {
    const wrapper = mount(DraftEditorPanel, {
      props: {
        draft: draft(),
        productContext,
        platformOptions,
        storeConfig: globalSellingStoreConfig,
        loading: false,
        embedded: true,
      },
    })

    expect(wrapper.find('[data-testid="cbt-destination-selector"]').exists()).toBe(false)
  })

  it('阻断并可移除不在当前授权绑定中的旧目的地', async () => {
    const currentDraft = cbtDraft()
    currentDraft.targetSites[0]!.sitesToSell = [
      { siteId: 'MLM', logisticType: 'remote' },
      { siteId: 'MCO', logisticType: 'remote' },
    ]
    const wrapper = mount(DraftEditorPanel, {
      props: {
        draft: currentDraft,
        productContext,
        platformOptions,
        storeConfig: globalSellingStoreConfig,
        loading: false,
        embedded: false,
        onUpdateSitesToSell: applySitesToSell,
      },
    })

    expect(wrapper.get('[data-testid="cbt-destination-error"]').text()).toContain('未授权')
    await wrapper.get('[data-testid="cbt-destination-remove-unauthorized"]').trigger('click')
    expect(currentDraft.targetSites[0]?.sitesToSell).toEqual([{ siteId: 'MLM', logisticType: 'remote' }])
    expect(wrapper.find('[data-testid="cbt-destination-error"]').exists()).toBe(false)
  })

  it('任一绑定为 Fully Managed 时整体阻断标准目的地流程', () => {
    const currentDraft = cbtDraft()
    currentDraft.targetSites[0]!.sitesToSell = [{ siteId: 'MLM', logisticType: 'remote' }]
    const wrapper = mount(DraftEditorPanel, {
      props: {
        draft: currentDraft,
        productContext,
        platformOptions,
        storeConfig: {
          mercadolibre: {
            ...globalSellingStoreConfig.mercadolibre,
            marketplace_bindings: [
              ...globalSellingStoreConfig.mercadolibre.marketplace_bindings,
              {
                seller_id: 'seller-fm',
                site_id: 'MCO',
                logistic_type: 'fulfillment',
                business_model: 'CBT CN Fulfillment Managed',
              },
            ],
          },
        },
        loading: false,
        embedded: false,
      },
    })

    expect(wrapper.get('[data-testid="cbt-destination-error"]').text()).toContain('Fully Managed')
    expect(wrapper.findAll('[data-testid="cbt-destination-checkbox"]')
      .every((checkbox) => (checkbox.element as HTMLInputElement).disabled)).toBe(true)
    expect(wrapper.get('button.btn-primary').attributes('disabled')).toBeDefined()
  })
})
