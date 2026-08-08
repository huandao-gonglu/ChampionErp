// @vitest-environment jsdom

import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

const fetchCategoryAttributeValues = vi.hoisted(() => vi.fn())
vi.mock('@/api/workflow/publishing', () => ({ fetchCategoryAttributeValues }))

import CategoryAttributesPanel from '@/components/domain/CategoryAttributesPanel.vue'
import { createEmptyDraftDetail, createEmptyDraftProductContext } from '@/constants/initialState'
import type { CategorySelection, DraftDetail, MarketplaceTargetSite } from '@/types/workflow'

const target: MarketplaceTargetSite = {
  platform: 'ozon',
  site: 'global',
  language: 'ru-RU',
  marketCurrency: 'RUB',
  listingCurrency: 'RUB',
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
  vi.clearAllMocks()
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
    draft.validationErrors = ['7236', '8229']

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
    expect(wrapper.findAll('button').some((button) => button.text() === 'Бренд')).toBe(false)

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

  it('修改必填属性时使已有类目预检结果失效', async () => {
    const draft = createEmptyDraftDetail('ozon')
    draft.draftId = 'draft-precheck'
    draft.site = 'global'
    draft.categoryId = '971049422'
    const category: CategorySelection = {
      platform: 'ozon',
      categoryId: '971049422',
      categoryPath: '汽车用品',
      requiredAttributes: [{ id: '7236', name: '模型名称', required: true, options: [] }],
      optionalAttributes: [],
      raw: {},
    }
    const wrapper = mount(CategoryAttributesPanel, {
      props: panelProps(draft, category),
    })

    const requiredButton = wrapper.findAll('button').find((button) => button.text().startsWith('必填属性'))
    await requiredButton!.trigger('click')
    await wrapper.get('[data-attribute-id="7236"]').setValue('Desk fan')

    expect(wrapper.emitted('invalidateCategoryPrecheck')).toHaveLength(1)
  })

  it('点击类目预检属性时复用待复核属性定位逻辑', async () => {
    const scrollIntoView = vi.fn()
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
      configurable: true,
      value: scrollIntoView,
    })
    const draft = createEmptyDraftDetail('ozon')
    draft.draftId = 'draft-category-precheck-focus'
    draft.site = 'global'
    draft.categoryId = '971049422'
    const category: CategorySelection = {
      platform: 'ozon',
      categoryId: '971049422',
      categoryPath: '汽车用品',
      requiredAttributes: [{ id: '9048', name: '体型', required: true, options: [] }],
      optionalAttributes: [],
      raw: {},
    }
    const wrapper = mount(CategoryAttributesPanel, {
      attachTo: document.body,
      props: {
        ...panelProps(draft, category),
        categoryPrecheck: {
          ok: false,
          errors: [],
          missingFields: ['attributes.9048'],
          checkedAt: '2026-08-04T00:00:00Z',
          raw: {},
        },
      },
    })

    expect(wrapper.find('[data-testid="required-attribute-fields"]').exists()).toBe(false)
    const issueButton = wrapper.findAll('button').find((button) => button.text() === 'attributes.9048')
    expect(issueButton).toBeDefined()
    await issueButton!.trigger('click')

    const input = wrapper.get<HTMLInputElement>('[data-attribute-id="9048"]')
    expect(wrapper.find('[data-testid="required-attribute-fields"]').exists()).toBe(true)
    expect(document.activeElement).toBe(input.element)
    expect(scrollIntoView).toHaveBeenCalledOnce()

    wrapper.unmount()
  })

  it('Ozon 字典属性只能保存平台返回的选项 ID', async () => {
    fetchCategoryAttributeValues.mockResolvedValueOnce([{
      id: '126745801',
      value: 'Нет бренда',
      info: 'Товар не имеет бренда',
    }])
    const draft = createEmptyDraftDetail('ozon')
    draft.draftId = 'draft-dictionary'
    draft.site = 'global'
    draft.categoryId = '94765'
    draft.attributes = { 85: '中性' }
    const category: CategorySelection = {
      platform: 'ozon',
      categoryId: '94765',
      categoryPath: '汽车用品',
      requiredAttributes: [{
        id: '85',
        name: 'Бренд',
        required: true,
        options: [],
        dictionaryId: '28732849',
        isDictionary: true,
      }],
      optionalAttributes: [],
      raw: {},
    }
    const wrapper = mount(CategoryAttributesPanel, {
      props: panelProps(draft, category),
    })

    const requiredButton = wrapper.findAll('button').find((button) => button.text().startsWith('必填属性'))
    await requiredButton!.trigger('click')
    expect(wrapper.text()).toContain('旧值“中性”不是平台选项')

    const selectedValue = wrapper.get<HTMLInputElement>('[data-attribute-id="85"]')
    expect(selectedValue.attributes()).toHaveProperty('readonly')
    await selectedValue.trigger('focus')
    await flushPromises()
    expect(fetchCategoryAttributeValues).toHaveBeenCalledWith('ozon', '94765', '85', 'global', '')

    const search = wrapper.get<HTMLInputElement>('[data-dictionary-search-id="85"]')
    expect(search.attributes('placeholder')).toContain('不会作为属性值保存')
    expect(draft.attributes['85']).toBe('中性')

    const option = wrapper.findAll('button').find((button) => button.text().includes('Нет бренда'))
    expect(option).toBeDefined()
    await option!.trigger('click')

    expect(draft.attributes['85']).toEqual({
      values: [{ dictionaryValueId: 126745801, value: 'Нет бренда' }],
    })
    expect(selectedValue.element.value).toBe('Нет бренда')
    expect(wrapper.emitted('invalidateCategoryPrecheck')).toHaveLength(1)
  })

  it('dictionary_id=0 的 Ozon 属性使用普通文本输入', async () => {
    const draft = createEmptyDraftDetail('ozon')
    draft.draftId = 'draft-free-text'
    draft.site = 'global'
    draft.categoryId = '91443'
    const category: CategorySelection = {
      platform: 'ozon',
      categoryId: '91443',
      categoryPath: 'Бытовая техника / Вентилятор',
      requiredAttributes: [{
        id: '9048',
        name: 'Название модели',
        required: true,
        options: [],
        dictionaryId: '0',
        isDictionary: true,
      }],
      optionalAttributes: [],
      raw: {},
    }
    const wrapper = mount(CategoryAttributesPanel, {
      props: panelProps(draft, category),
    })

    const requiredButton = wrapper.findAll('button').find((button) => button.text().startsWith('必填属性'))
    await requiredButton!.trigger('click')
    const input = wrapper.get<HTMLInputElement>('[data-attribute-id="9048"]')

    expect(input.attributes('placeholder')).toBe('请输入属性值')
    await input.setValue('F30')
    expect(draft.attributes['9048']).toBe('F30')
    expect(fetchCategoryAttributeValues).not.toHaveBeenCalled()
  })
})
