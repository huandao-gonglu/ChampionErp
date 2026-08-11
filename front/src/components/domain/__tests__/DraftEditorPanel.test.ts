// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import DraftEditorPanel from '@/components/domain/DraftEditorPanel.vue'
import type { DraftDetail, DraftProductContext, MarketplaceOption } from '@/types/workflow'

const platformOptions: MarketplaceOption[] = [
  {
    key: 'mercadolibre',
    label: '美客多',
    sites: [
      { key: 'CBT', code: 'CBT', label: '全局', language: 'es', marketCurrency: 'USD', listingCurrency: 'USD' },
      { key: 'MLM', code: 'MLM', label: '墨西哥', language: 'es', marketCurrency: 'MXN', listingCurrency: 'MXN' },
      { key: 'MLB', code: 'MLB', label: '巴西', language: 'pt-BR', marketCurrency: 'BRL', listingCurrency: 'BRL' },
    ],
  },
  {
    key: 'ozon',
    label: 'Ozon',
    sites: [
      { key: 'global', code: 'global', label: '俄罗斯', language: 'ru-RU', marketCurrency: 'RUB', listingCurrency: '' },
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
      marketCurrency: 'MXN',
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
    expect(languageSelect.findAll('option').map((option) => option.attributes('value'))).toEqual(['', 'es', 'pt-BR', 'ru-RU'])

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

    expect(wrapper.text()).toContain('美客多 · 全局（CBT）')
    expect(wrapper.text()).toContain('美客多 · 墨西哥（MLM）')
    expect(wrapper.text()).not.toContain('美客多 · 巴西（MLB）')
    expect(wrapper.text()).not.toContain('Ozon · 俄罗斯（global）')

    const targetCheckboxes = wrapper.findAll('[data-testid="draft-target-checkbox"]')
    expect(targetCheckboxes).toHaveLength(2)
    expect((targetCheckboxes[0].element as HTMLInputElement).checked).toBe(false)
    expect((targetCheckboxes[1].element as HTMLInputElement).checked).toBe(true)

    await targetCheckboxes[0].setValue(true)
    await wrapper.get('[data-testid="draft-market-select-save"]').trigger('click')

    expect(wrapper.emitted('updateTargets')?.[0]?.[0]).toEqual(currentDraft)
    expect(wrapper.emitted('updateTargets')?.[0]?.[1]).toEqual([
      expect.objectContaining({ platform: 'mercadolibre', site: 'CBT', language: 'es', listingCurrency: 'USD' }),
      expect.objectContaining({ platform: 'mercadolibre', site: 'MLM', language: 'es', listingCurrency: 'MXN' }),
    ])
  })
})
