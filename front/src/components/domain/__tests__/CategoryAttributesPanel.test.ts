// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import CategoryAttributesPanel from '@/components/domain/CategoryAttributesPanel.vue'
import { createEmptyDraftDetail, createEmptyDraftProductContext } from '@/constants/initialState'
import type { CategorySelection, DraftDetail, MarketplaceTargetSite } from '@/types/workflow'

const target: MarketplaceTargetSite = {
  platform: 'ozon',
  site: 'global',
  language: 'ru-RU',
  currency: 'RUB',
}

function panelProps(draft: DraftDetail, category: CategorySelection | null) {
  return {
    draft,
    productContext: createEmptyDraftProductContext(),
    publishTargets: [target],
    selectedPublishTarget: target,
    platformOptions: [],
    category,
    categoryQuery: '',
    categoryResults: [],
    categoryAttributeTranslations: {},
    categoryAttributeTranslationsSource: '',
    categoryAttributeTranslating: false,
    categoryResultTranslations: {},
    categoryResultTranslationsSource: '',
    categoryResultTranslating: false,
    categoryPrecheck: null,
    precheck: null,
    loading: false,
  }
}

afterEach(() => {
  document.body.innerHTML = ''
})

describe('CategoryAttributesPanel', () => {
  it('uses separate category and attribute translation triggers', async () => {
    const draft = createEmptyDraftDetail('ozon')
    draft.draftId = 'draft-translation'
    draft.site = 'global'
    draft.categoryId = '971049422'
    const category: CategorySelection = {
      platform: 'ozon',
      categoryId: '971049422',
      categoryPath: 'Автотовары / Запчасти',
      requiredAttributes: [{ id: '7236', name: 'Название модели', required: true, options: [] }],
      optionalAttributes: [],
      raw: {},
    }
    const wrapper = mount(CategoryAttributesPanel, {
      props: {
        ...panelProps(draft, category),
        categoryResults: [{ id: '971049422', name: 'Запчасти', path: 'Автотовары / Запчасти', raw: {} }],
      },
    })

    const categoryButton = wrapper.findAll('button').find((button) => button.text() === '翻译候选类目')
    const attributeButton = wrapper.findAll('button').find((button) => button.text() === '翻译平台属性')
    expect(categoryButton).toBeDefined()
    expect(attributeButton).toBeDefined()

    await categoryButton!.trigger('click')
    await attributeButton!.trigger('click')

    expect(wrapper.emitted('translateCategoryResults')).toHaveLength(1)
    expect(wrapper.emitted('translateCategoryAttributes')).toHaveLength(1)
  })

  it('平台属性定义未加载时不再用草稿属性生成临时输入框', async () => {
    const draft = createEmptyDraftDetail('ozon')
    draft.draftId = 'draft-schema-error'
    draft.site = 'global'
    draft.categoryId = '971049422'
    draft.attributes = { BRAND: '卡西道夫', 7236: '旧值' }
    draft.validationErrors = ['7236']

    const wrapper = mount(CategoryAttributesPanel, {
      props: {
        ...panelProps(draft, null),
        categoryAttributeError: '平台类目属性接口超时',
      },
    })

    expect(wrapper.text()).toContain('平台属性定义未加载')
    expect(wrapper.text()).toContain('平台类目属性接口超时')
    expect(wrapper.find('[data-attribute-id]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('待复核属性')

    const retryButton = wrapper.findAll('button').find((button) => button.text() === '重新加载')
    expect(retryButton).toBeDefined()
    await retryButton!.trigger('click')
    expect(wrapper.emitted('applyCategory')).toHaveLength(1)
  })

  it('点击待复核属性时展开对应分组并定位平台定义中的输入框', async () => {
    const scrollIntoView = vi.fn()
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
      configurable: true,
      value: scrollIntoView,
    })
    const draft = createEmptyDraftDetail('ozon')
    draft.draftId = 'draft-review'
    draft.site = 'global'
    draft.categoryId = '971049422'
    draft.validationErrors = ['7236']
    const category: CategorySelection = {
      platform: 'ozon',
      categoryId: '971049422',
      categoryPath: 'Автотовары / Запчасти',
      requiredAttributes: [{ id: '7236', name: 'Название модели', required: true, options: [] }],
      optionalAttributes: [{ id: '8229', name: 'Бренд', required: false, options: [] }],
      raw: {},
    }
    const wrapper = mount(CategoryAttributesPanel, {
      attachTo: document.body,
      props: panelProps(draft, category),
    })

    expect(wrapper.find('[data-testid="required-attribute-fields"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="optional-attribute-fields"]').exists()).toBe(false)

    const reviewButton = wrapper.findAll('button').find((button) => button.text() === 'Название модели')
    expect(reviewButton).toBeDefined()
    await reviewButton!.trigger('click')

    const input = wrapper.get<HTMLInputElement>('[data-attribute-id="7236"]')
    expect(wrapper.find('[data-testid="required-attribute-fields"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="optional-attribute-fields"]').exists()).toBe(false)
    expect(document.activeElement).toBe(input.element)
    expect(scrollIntoView).toHaveBeenCalledOnce()

    wrapper.unmount()
  })
})
