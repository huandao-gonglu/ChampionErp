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
  listingCurrency: 'RUB',
}

const yandexTarget: MarketplaceTargetSite = {
  platform: 'yandex',
  site: 'global',
  language: 'ru',
  listingCurrency: 'RUB',
}

const mercadoTarget: MarketplaceTargetSite = {
  platform: 'mercadolibre',
  site: 'CBT',
  language: 'es',
  listingCurrency: 'USD',
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
  vi.useRealTimers()
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
      fetchedAt: '2026-08-05T00:00:00Z',
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
      fetchedAt: '2026-08-05T00:00:00Z',
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
      fetchedAt: '2026-08-05T00:00:00Z',
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

  it('人工修改 Mercado 属性后清除该属性的待复核标记', async () => {
    const draft = createEmptyDraftDetail('mercadolibre')
    draft.draftId = 'draft-mercado-voltage-review'
    draft.site = 'CBT'
    draft.categoryId = 'CBT455865'
    draft.attributes = { VOLTAGE: '110V' }
    draft.validationErrors = ['VOLTAGE', 'MODEL']
    const category: CategorySelection = {
      platform: 'mercadolibre',
      categoryId: 'CBT455865',
      categoryPath: 'Computers / Portable Fans',
      requiredAttributes: [{ id: 'VOLTAGE', name: 'Voltage', required: true, options: [] }],
      optionalAttributes: [],
      fetchedAt: '2026-08-28T00:00:00Z',
      raw: {},
    }
    const wrapper = mount(CategoryAttributesPanel, {
      props: {
        ...panelProps(draft, category),
        publishTargets: [mercadoTarget],
        selectedPublishTarget: mercadoTarget,
      },
    })

    await wrapper.findAll('button').find((button) => button.text().startsWith('必填属性'))!.trigger('click')
    expect(wrapper.text()).toContain('待复核属性')
    expect(wrapper.text()).toContain('AI 暂无法从商品信息判断，请人工确认。')

    await wrapper.get<HTMLInputElement>('[data-attribute-id="VOLTAGE"]').setValue('110/220V')

    expect(draft.attributes.VOLTAGE).toBe('110/220V')
    expect(draft.validationErrors).toEqual(['MODEL'])
    expect(wrapper.text()).not.toContain('待复核属性')
    expect(wrapper.text()).not.toContain('AI 暂无法从商品信息判断，请人工确认。')
    expect(wrapper.emitted('invalidateCategoryPrecheck')).toHaveLength(1)
  })

  it('Mercado 品牌和型号映射到草稿根字段而不保留重复属性值', async () => {
    const draft = createEmptyDraftDetail('mercadolibre')
    draft.draftId = 'draft-mercado-brand-model'
    draft.site = 'CBT'
    draft.categoryId = 'CBT455865'
    draft.brand = 'Root Brand'
    draft.model = 'Root Model'
    draft.attributes = {
      BRAND: { values: [{ dictionaryValueId: 'stale-brand-id', value: 'Root Brand' }] },
      MODEL: 'Stale Model',
    }
    const category: CategorySelection = {
      platform: 'mercadolibre',
      categoryId: 'CBT455865',
      categoryPath: 'Computers / Portable Fans',
      requiredAttributes: [
        { id: 'BRAND', name: 'Brand', required: true, options: [] },
        { id: 'MODEL', name: 'Model', required: true, options: [] },
      ],
      optionalAttributes: [],
      fetchedAt: '2026-08-28T00:00:00Z',
      raw: {},
    }
    const wrapper = mount(CategoryAttributesPanel, {
      props: {
        ...panelProps(draft, category),
        publishTargets: [mercadoTarget],
        selectedPublishTarget: mercadoTarget,
      },
    })

    await wrapper.findAll('button').find((button) => button.text().startsWith('必填属性'))!.trigger('click')
    const brandInput = wrapper.get<HTMLInputElement>('[data-attribute-id="BRAND"]')
    const modelInput = wrapper.get<HTMLInputElement>('[data-attribute-id="MODEL"]')
    expect(brandInput.element.value).toBe('Root Brand')
    expect(modelInput.element.value).toBe('Root Model')

    await brandInput.setValue('New Brand')
    await modelInput.setValue('New Model')

    expect(draft.brand).toBe('New Brand')
    expect(draft.model).toBe('New Model')
    expect(draft.attributes.BRAND).toBeUndefined()
    expect(draft.attributes.MODEL).toBeUndefined()
    expect(wrapper.emitted('invalidateCategoryPrecheck')).toHaveLength(2)
  })

  it('Mercado 严格品牌复用本地候选 picker，MODEL 仍使用普通输入', async () => {
    const draft = createEmptyDraftDetail('mercadolibre')
    draft.draftId = 'draft-mercado-local-brand'
    draft.site = 'CBT'
    draft.categoryId = 'CBT455865'
    draft.brand = 'Old Brand'
    draft.model = 'Root Model'
    draft.attributes = { MODEL: 'Stale Model' }
    const category: CategorySelection = {
      platform: 'mercadolibre',
      categoryId: 'CBT455865',
      categoryPath: 'Computers / Portable Fans',
      requiredAttributes: [
        {
          id: 'BRAND',
          name: 'Brand',
          required: true,
          options: ['Xiaomi', 'Baseus'],
          valueMode: 'strict_enum',
          allowCustomValues: false,
        },
        {
          id: 'MODEL',
          name: 'Model',
          required: true,
          options: ['M1', 'M2'],
          valueMode: 'strict_enum',
          allowCustomValues: false,
        },
      ],
      optionalAttributes: [],
      fetchedAt: '2026-08-29T00:00:00Z',
      raw: {},
    }
    const wrapper = mount(CategoryAttributesPanel, {
      props: {
        ...panelProps(draft, category),
        publishTargets: [mercadoTarget],
        selectedPublishTarget: mercadoTarget,
      },
    })

    await wrapper.findAll('button').find((button) => button.text().startsWith('必填属性'))!.trigger('click')
    const brandPicker = wrapper.get<HTMLInputElement>('input[data-attribute-id="BRAND"]')
    const modelInput = wrapper.get<HTMLInputElement>('input[data-attribute-id="MODEL"]')
    expect(brandPicker.attributes()).toHaveProperty('readonly')
    expect(brandPicker.element.value).toBe('Old Brand')
    expect(modelInput.element.value).toBe('Root Model')

    await brandPicker.trigger('focus')
    const baseus = wrapper.findAll('button').find((button) => button.text().includes('Baseus'))
    expect(baseus).toBeDefined()
    await baseus!.trigger('click')

    expect(draft.brand).toBe('Baseus')
    expect(draft.attributes.BRAND).toBeUndefined()
    expect(fetchCategoryAttributeValues).not.toHaveBeenCalled()
  })

  it('Mercado 严格品牌可搜索并分页选择第 51 项，同时保存与文案绑定的平台 ID', async () => {
    vi.useFakeTimers()
    fetchCategoryAttributeValues
      .mockResolvedValueOnce({
        values: [{ id: 'brand-1', value: 'Brand 1', info: '' }],
        nextCursor: 'offset:50',
        hasMore: true,
        complete: false,
      })
      .mockResolvedValueOnce({
        values: [{ id: 'brand-50', value: 'Remote Brand 50', info: '' }],
        nextCursor: 'offset:50',
        hasMore: true,
        complete: false,
      })
      .mockResolvedValueOnce({
        values: [{ id: 'brand-51', value: 'Remote Brand 51', info: '' }],
        nextCursor: '',
        hasMore: false,
        complete: true,
      })
    const draft = createEmptyDraftDetail('mercadolibre')
    draft.draftId = 'draft-mercado-remote-brand'
    draft.site = 'CBT'
    draft.categoryId = 'CBT455865'
    const category: CategorySelection = {
      platform: 'mercadolibre',
      categoryId: 'CBT455865',
      categoryPath: 'Computers / Portable Fans',
      requiredAttributes: [{
        id: 'BRAND',
        name: 'Brand',
        required: true,
        options: ['Brand 1'],
        valueMode: 'strict_enum',
        allowCustomValues: false,
        hasMoreValues: true,
      }],
      optionalAttributes: [],
      fetchedAt: '2026-08-29T00:00:00Z',
      raw: {},
    }
    const wrapper = mount(CategoryAttributesPanel, {
      props: {
        ...panelProps(draft, category),
        publishTargets: [mercadoTarget],
        selectedPublishTarget: mercadoTarget,
      },
    })

    await wrapper.findAll('button').find((button) => button.text().startsWith('必填属性'))!.trigger('click')
    const brandPicker = wrapper.get<HTMLInputElement>('input[data-attribute-id="BRAND"]')
    await brandPicker.trigger('focus')
    await flushPromises()

    expect(fetchCategoryAttributeValues).toHaveBeenNthCalledWith(
      1,
      'mercadolibre',
      'CBT455865',
      'BRAND',
      'CBT',
      '',
      50,
      '',
    )
    const search = wrapper.get<HTMLInputElement>('[data-dictionary-search-id="BRAND"]')
    expect(search.attributes('placeholder')).toContain('不会作为属性值保存')
    await search.setValue('Remote Brand')
    await vi.advanceTimersByTimeAsync(250)
    await flushPromises()

    expect(fetchCategoryAttributeValues).toHaveBeenNthCalledWith(
      2,
      'mercadolibre',
      'CBT455865',
      'BRAND',
      'CBT',
      'Remote Brand',
      50,
      '',
    )
    expect(wrapper.findAll('button').some((button) => button.text() === '使用此值')).toBe(false)

    await wrapper.findAll('button').find((button) => button.text() === '加载更多平台选项')!.trigger('click')
    await flushPromises()
    expect(fetchCategoryAttributeValues).toHaveBeenNthCalledWith(
      3,
      'mercadolibre',
      'CBT455865',
      'BRAND',
      'CBT',
      'Remote Brand',
      50,
      'offset:50',
    )

    await wrapper.findAll('button').find((button) => button.text().includes('Remote Brand 51'))!.trigger('click')

    expect(draft.brand).toBe('Remote Brand 51')
    expect(draft.attributes.BRAND).toEqual({
      values: [{ dictionaryValueId: 'brand-51', value: 'Remote Brand 51' }],
    })
    expect(brandPicker.element.value).toBe('Remote Brand 51')
    expect(wrapper.emitted('invalidateCategoryPrecheck')).toHaveLength(1)

    await wrapper.findAll('button').find((button) => button.text() === '清除已选')!.trigger('click')
    expect(draft.brand).toBe('')
    expect(draft.attributes.BRAND).toBeUndefined()
    expect(wrapper.emitted('invalidateCategoryPrecheck')).toHaveLength(2)
    wrapper.unmount()
  })

  it('Mercado 严格品牌显示旧非法根值并要求重新选择', async () => {
    const draft = createEmptyDraftDetail('mercadolibre')
    draft.draftId = 'draft-mercado-invalid-brand'
    draft.site = 'CBT'
    draft.categoryId = 'CBT455865'
    draft.brand = '蔚小电'
    draft.attributes.BRAND = {
      values: [{ dictionaryValueId: 'other-brand-id', value: 'Other Brand' }],
    }
    const category: CategorySelection = {
      platform: 'mercadolibre',
      categoryId: 'CBT455865',
      categoryPath: 'Computers / Portable Fans',
      requiredAttributes: [{
        id: 'BRAND',
        name: 'Brand',
        required: true,
        options: ['Generic'],
        valueMode: 'strict_enum',
        allowCustomValues: false,
        hasMoreValues: true,
      }],
      optionalAttributes: [],
      fetchedAt: '2026-08-29T00:00:00Z',
      raw: {},
    }
    const wrapper = mount(CategoryAttributesPanel, {
      props: {
        ...panelProps(draft, category),
        publishTargets: [mercadoTarget],
        selectedPublishTarget: mercadoTarget,
      },
    })

    await wrapper.findAll('button').find((button) => button.text().startsWith('必填属性'))!.trigger('click')
    const brandPicker = wrapper.get<HTMLInputElement>('input[data-attribute-id="BRAND"]')
    expect(brandPicker.element.value).toBe('蔚小电')
    expect(wrapper.text()).toContain('旧值“蔚小电”不是平台选项，请重新选择。')
  })

  it('Mercado 品牌无平台候选时手工编辑根值会清理旧选择元数据', async () => {
    const draft = createEmptyDraftDetail('mercadolibre')
    draft.draftId = 'draft-mercado-manual-brand'
    draft.site = 'CBT'
    draft.categoryId = 'CBT455865'
    draft.brand = 'Old Brand'
    draft.attributes.BRAND = {
      values: [{ dictionaryValueId: 'old-brand-id', value: 'Old Brand' }],
    }
    const category: CategorySelection = {
      platform: 'mercadolibre',
      categoryId: 'CBT455865',
      categoryPath: 'Computers / Portable Fans',
      requiredAttributes: [{
        id: 'BRAND',
        name: 'Brand',
        required: true,
        options: [],
        valueMode: 'strict_enum',
        allowCustomValues: false,
        hasMoreValues: false,
      }],
      optionalAttributes: [],
      fetchedAt: '2026-08-29T00:00:00Z',
      raw: {},
    }
    const wrapper = mount(CategoryAttributesPanel, {
      props: {
        ...panelProps(draft, category),
        publishTargets: [mercadoTarget],
        selectedPublishTarget: mercadoTarget,
      },
    })

    await wrapper.findAll('button').find((button) => button.text().startsWith('必填属性'))!.trigger('click')
    const brandInput = wrapper.get<HTMLInputElement>('input[data-attribute-id="BRAND"]')
    expect(brandInput.attributes()).not.toHaveProperty('readonly')
    await brandInput.setValue('Edited Brand')
    expect(draft.brand).toBe('Edited Brand')
    expect(draft.attributes.BRAND).toBeUndefined()
  })

  it('Mercado 包装属性映射并修改草稿的规范化包装尺寸', async () => {
    const draft = createEmptyDraftDetail('mercadolibre')
    draft.draftId = 'draft-mercado-package-dimensions'
    draft.site = 'CBT'
    draft.categoryId = 'CBT455865'
    draft.packageDimensions = {
      lengthCm: '21',
      widthCm: '15.5',
      heightCm: '12',
      weightKg: '0.419',
    }
    draft.attributes = {
      PACKAGE_LENGTH: '旧的重复值',
      PACKAGE_WIDTH: '旧的重复值',
      PACKAGE_HEIGHT: '旧的重复值',
      PACKAGE_WEIGHT: '旧的重复值',
    }
    const category: CategorySelection = {
      platform: 'mercadolibre',
      categoryId: 'CBT455865',
      categoryPath: 'Computers / Portable Fans',
      requiredAttributes: [
        { id: 'PACKAGE_LENGTH', name: 'Package length', required: true, options: [] },
        { id: 'PACKAGE_WIDTH', name: 'Package width', required: true, options: [] },
        { id: 'PACKAGE_HEIGHT', name: 'Package height', required: true, options: [] },
        { id: 'PACKAGE_WEIGHT', name: 'Package weight', required: true, options: [] },
      ],
      optionalAttributes: [],
      fetchedAt: '2026-08-28T00:00:00Z',
      raw: {},
    }
    const wrapper = mount(CategoryAttributesPanel, {
      props: {
        ...panelProps(draft, category),
        publishTargets: [mercadoTarget],
        selectedPublishTarget: mercadoTarget,
      },
    })

    await wrapper.findAll('button').find((button) => button.text().startsWith('必填属性'))!.trigger('click')

    expect(wrapper.get<HTMLInputElement>('[data-attribute-id="PACKAGE_LENGTH"]').element.value).toBe('21')
    expect(wrapper.get<HTMLInputElement>('[data-attribute-id="PACKAGE_WIDTH"]').element.value).toBe('15.5')
    expect(wrapper.get<HTMLInputElement>('[data-attribute-id="PACKAGE_HEIGHT"]').element.value).toBe('12')
    expect(wrapper.get<HTMLInputElement>('[data-attribute-id="PACKAGE_WEIGHT"]').element.value).toBe('0.419')
    expect(wrapper.text()).toContain('来自草稿包装尺寸')

    const updates = [
      ['PACKAGE_LENGTH', 'lengthCm', '23'],
      ['PACKAGE_WIDTH', 'widthCm', '16'],
      ['PACKAGE_HEIGHT', 'heightCm', '13'],
      ['PACKAGE_WEIGHT', 'weightKg', '0.5'],
    ] as const
    for (const [attributeId, field, value] of updates) {
      await wrapper.get<HTMLInputElement>(`[data-attribute-id="${attributeId}"]`).setValue(value)
      expect(draft.packageDimensions[field]).toBe(value)
      expect(draft.attributes[attributeId]).toBeUndefined()
    }

    expect(wrapper.emitted('updatePackageDimension')).toEqual(updates.map(([, field, value]) => [field, value]))
    expect(wrapper.emitted('invalidateCategoryPrecheck')).toHaveLength(updates.length)
  })

  it('Mercado 属性编辑器隐藏发布编译器管理的影子字段', async () => {
    const draft = createEmptyDraftDetail('mercadolibre')
    draft.draftId = 'draft-mercado-managed-attributes'
    draft.site = 'CBT'
    draft.categoryId = 'CBT455865'
    const category: CategorySelection = {
      platform: 'mercadolibre',
      categoryId: 'CBT455865',
      categoryPath: 'Computers / Portable Fans',
      requiredAttributes: [
        { id: 'PACKAGE_LENGTH', name: 'Package length', required: true, options: [] },
        { id: 'ITEM_CONDITION', name: 'Item condition', required: true, options: [] },
      ],
      optionalAttributes: [
        { id: 'SELLER_SKU', name: 'Seller SKU', required: false, options: [] },
        { id: 'GTIN', name: 'GTIN', required: false, options: [] },
        { id: 'VOLTAGE', name: 'Voltage', required: false, options: [] },
      ],
      fetchedAt: '2026-08-28T00:00:00Z',
      raw: {},
    }
    const wrapper = mount(CategoryAttributesPanel, {
      props: {
        ...panelProps(draft, category),
        publishTargets: [mercadoTarget],
        selectedPublishTarget: mercadoTarget,
      },
    })

    expect(wrapper.text()).toContain('必填属性 1 个')
    expect(wrapper.text()).toContain('可选属性 1 个')
    await wrapper.findAll('button').find((button) => button.text().startsWith('必填属性'))!.trigger('click')
    await wrapper.findAll('button').find((button) => button.text().startsWith('可选属性'))!.trigger('click')

    expect(wrapper.find('[data-attribute-id="PACKAGE_LENGTH"]').exists()).toBe(true)
    expect(wrapper.find('[data-attribute-id="VOLTAGE"]').exists()).toBe(true)
    expect(wrapper.find('[data-attribute-id="ITEM_CONDITION"]').exists()).toBe(false)
    expect(wrapper.find('[data-attribute-id="SELLER_SKU"]').exists()).toBe(false)
    expect(wrapper.find('[data-attribute-id="GTIN"]').exists()).toBe(false)
  })

  it('Mercado 开放枚举超过本地预览时可搜索完整候选或直接填写自定义值', async () => {
    fetchCategoryAttributeValues.mockResolvedValueOnce({
      values: [{ id: 'remote-51', value: 'Remote material', info: '' }],
      nextCursor: '',
      hasMore: false,
      complete: true,
    })
    const draft = createEmptyDraftDetail('mercadolibre')
    draft.draftId = 'draft-mercado-open-enum'
    draft.site = 'CBT'
    draft.categoryId = 'CBT455865'
    const category: CategorySelection = {
      platform: 'mercadolibre',
      categoryId: 'CBT455865',
      categoryPath: 'Computers / Portable Fans',
      requiredAttributes: [{
        id: 'MATERIAL',
        name: 'Material',
        required: true,
        options: ['Plastic'],
        valueType: 'string',
        valueMode: 'open_enum',
        hasMoreValues: true,
      }],
      optionalAttributes: [],
      fetchedAt: '2026-08-28T00:00:00Z',
      raw: {},
    }
    const wrapper = mount(CategoryAttributesPanel, {
      props: {
        ...panelProps(draft, category),
        publishTargets: [mercadoTarget],
        selectedPublishTarget: mercadoTarget,
      },
    })

    await wrapper.findAll('button').find((button) => button.text().startsWith('必填属性'))!.trigger('click')
    const valueInput = wrapper.get<HTMLInputElement>('[data-attribute-id="MATERIAL"]')
    expect(valueInput.attributes()).toHaveProperty('readonly')
    await valueInput.trigger('focus')
    await flushPromises()

    expect(fetchCategoryAttributeValues).toHaveBeenCalledWith(
      'mercadolibre',
      'CBT455865',
      'MATERIAL',
      'CBT',
      '',
      50,
      '',
    )
    expect(wrapper.text()).toContain('Remote material')

    const search = wrapper.get<HTMLInputElement>('[data-dictionary-search-id="MATERIAL"]')
    await search.setValue('Bamboo composite')
    const useCustom = wrapper.findAll('button').find((button) => button.text() === '使用此值')
    expect(useCustom).toBeDefined()
    await useCustom!.trigger('click')

    expect(draft.attributes.MATERIAL).toBe('Bamboo composite')
    expect(valueInput.element.value).toBe('Bamboo composite')
    expect(wrapper.emitted('invalidateCategoryPrecheck')).toHaveLength(1)
    wrapper.unmount()
  })

  it('Mercado collection 属性可同时选择建议值并添加多个自定义值', async () => {
    const draft = createEmptyDraftDetail('mercadolibre')
    draft.draftId = 'draft-mercado-multivalue'
    draft.site = 'CBT'
    draft.categoryId = 'CBT455865'
    const category: CategorySelection = {
      platform: 'mercadolibre',
      categoryId: 'CBT455865',
      categoryPath: 'Computers / Portable Fans',
      requiredAttributes: [{
        id: 'COMPATIBLE_DEVICES',
        name: 'Compatible devices',
        required: true,
        options: ['Phone', 'Tablet'],
        valueType: 'string',
        valueMode: 'open_enum',
        isCollection: true,
        maxValueCount: 3,
      }],
      optionalAttributes: [],
      fetchedAt: '2026-08-28T00:00:00Z',
      raw: {},
    }
    const wrapper = mount(CategoryAttributesPanel, {
      props: {
        ...panelProps(draft, category),
        publishTargets: [mercadoTarget],
        selectedPublishTarget: mercadoTarget,
      },
    })

    await wrapper.findAll('button').find((button) => button.text().startsWith('必填属性'))!.trigger('click')
    await wrapper.get<HTMLInputElement>('[data-attribute-id="COMPATIBLE_DEVICES"]').trigger('focus')

    await wrapper.findAll('button').find((button) => button.text().includes('Phone'))!.trigger('click')
    await wrapper.findAll('button').find((button) => button.text().includes('Tablet'))!.trigger('click')
    const search = wrapper.get<HTMLInputElement>('[data-dictionary-search-id="COMPATIBLE_DEVICES"]')
    await search.setValue('Laptop')
    await wrapper.findAll('button').find((button) => button.text() === '添加')!.trigger('click')

    expect(draft.attributes.COMPATIBLE_DEVICES).toEqual({
      values: [
        { value: 'Phone' },
        { value: 'Tablet' },
        { value: 'Laptop' },
      ],
    })
    expect(wrapper.get<HTMLInputElement>('[data-attribute-id="COMPATIBLE_DEVICES"]').element.value).toBe('已选 3 项')
    expect(wrapper.findAll('button').filter((button) => button.attributes('aria-label') === '移除选项')).toHaveLength(3)
    expect(fetchCategoryAttributeValues).not.toHaveBeenCalled()
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
      fetchedAt: '2026-08-05T00:00:00Z',
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
    fetchCategoryAttributeValues.mockResolvedValueOnce({
      values: [{
        id: '126745801',
        value: 'Нет бренда',
        info: 'Товар не имеет бренда',
      }],
      nextCursor: '',
      hasMore: false,
      complete: true,
    })
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
      fetchedAt: '2026-08-05T00:00:00Z',
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
    expect(fetchCategoryAttributeValues).toHaveBeenCalledWith('ozon', '94765', '85', 'global', '', 50, '')

    const search = wrapper.get<HTMLInputElement>('[data-dictionary-search-id="85"]')
    expect(search.attributes('placeholder')).toContain('不会作为属性值保存')
    expect(draft.attributes['85']).toBe('中性')

    const option = wrapper.findAll('button').find((button) => button.text().includes('Нет бренда'))
    expect(option).toBeDefined()
    await option!.trigger('click')

    expect(draft.attributes['85']).toEqual({
      values: [{ dictionaryValueId: '126745801', value: 'Нет бренда' }],
    })
    expect(selectedValue.element.value).toBe('Нет бренда')
    expect(wrapper.emitted('invalidateCategoryPrecheck')).toHaveLength(1)
  })

  it('品牌字典可按游标加载后续候选并选择无品牌', async () => {
    fetchCategoryAttributeValues
      .mockResolvedValueOnce({
        values: [{ id: 'brand-1', value: 'Alpha', info: '' }],
        nextCursor: 'brand-1',
        hasMore: true,
        complete: false,
      })
      .mockResolvedValueOnce({
        values: [{ id: 'no-brand-id', value: 'Нет бренда', info: '' }],
        nextCursor: '',
        hasMore: false,
        complete: true,
      })
    const draft = createEmptyDraftDetail('ozon')
    draft.draftId = 'draft-paginated-brand'
    draft.site = 'global'
    draft.categoryId = '94765'
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
      fetchedAt: '2026-08-05T00:00:00Z',
      raw: {},
    }
    const wrapper = mount(CategoryAttributesPanel, {
      props: panelProps(draft, category),
    })

    await wrapper.findAll('button').find((button) => button.text().startsWith('必填属性'))!.trigger('click')
    await wrapper.get<HTMLInputElement>('[data-attribute-id="85"]').trigger('focus')
    await flushPromises()

    const loadMore = wrapper.findAll('button').find((button) => button.text() === '加载更多平台选项')
    expect(loadMore).toBeDefined()
    await loadMore!.trigger('click')
    await flushPromises()

    expect(fetchCategoryAttributeValues).toHaveBeenNthCalledWith(
      2,
      'ozon',
      '94765',
      '85',
      'global',
      '',
      50,
      'brand-1',
    )
    expect(wrapper.text()).toContain('Alpha')
    const noBrand = wrapper.findAll('button').find((button) => button.text().includes('Нет бренда'))
    expect(noBrand).toBeDefined()
    await noBrand!.trigger('click')
    expect(draft.attributes['85']).toEqual({
      values: [{ dictionaryValueId: 'no-brand-id', value: 'Нет бренда' }],
    })
  })

  it('Ozon 单字符字典查询在前端提示且不请求平台', async () => {
    fetchCategoryAttributeValues.mockResolvedValueOnce({
      values: [{ id: 'brand-1', value: 'Alpha', info: '' }],
      nextCursor: '',
      hasMore: false,
      complete: true,
    })
    const draft = createEmptyDraftDetail('ozon')
    draft.draftId = 'draft-short-brand-query'
    draft.site = 'global'
    draft.categoryId = '94765'
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
      fetchedAt: '2026-08-05T00:00:00Z',
      raw: {},
    }
    const wrapper = mount(CategoryAttributesPanel, {
      props: panelProps(draft, category),
    })

    await wrapper.findAll('button').find((button) => button.text().startsWith('必填属性'))!.trigger('click')
    await wrapper.get<HTMLInputElement>('[data-attribute-id="85"]').trigger('focus')
    await flushPromises()
    expect(fetchCategoryAttributeValues).toHaveBeenCalledTimes(1)

    await wrapper.get<HTMLInputElement>('[data-dictionary-search-id="85"]').setValue('A')
    await flushPromises()

    expect(fetchCategoryAttributeValues).toHaveBeenCalledTimes(1)
    expect(wrapper.text()).toContain('Ozon 平台枚举搜索至少需要 2 个字符')
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
      fetchedAt: '2026-08-05T00:00:00Z',
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

  it('Yandex 大枚举值 ID 按字符串保存，不经过 Number 精度截断', async () => {
    const hugeId = '9007199254740993123'
    expect(Number.isSafeInteger(Number(hugeId))).toBe(false)
    fetchCategoryAttributeValues.mockResolvedValueOnce({
      values: [{
        id: hugeId,
        value: 'Белый',
        info: '',
      }],
      nextCursor: '',
      hasMore: false,
      complete: true,
    })
    const draft = createEmptyDraftDetail('yandex')
    draft.draftId = 'draft-yandex-dictionary'
    draft.site = 'global'
    draft.categoryId = '91596'
    const category: CategorySelection = {
      platform: 'yandex',
      categoryId: '91596',
      categoryPath: 'Бытовая техника',
      requiredAttributes: [{
        id: '85',
        name: 'Цвет',
        required: true,
        options: [],
        dictionaryId: '1494',
        isDictionary: true,
        isCollection: true,
        maxValueCount: 2,
      }],
      optionalAttributes: [],
      fetchedAt: '2026-08-05T00:00:00Z',
      raw: {},
    }
    const wrapper = mount(CategoryAttributesPanel, {
      props: {
        ...panelProps(draft, category),
        publishTargets: [yandexTarget],
        selectedPublishTarget: yandexTarget,
      },
    })

    const requiredButton = wrapper.findAll('button').find((button) => button.text().startsWith('必填属性'))
    await requiredButton!.trigger('click')
    await wrapper.get<HTMLInputElement>('[data-attribute-id="85"]').trigger('focus')
    await flushPromises()

    const option = wrapper.findAll('button').find((button) => button.text().includes('Белый'))
    await option!.trigger('click')

    expect(draft.attributes['85']).toEqual({
      values: [{ dictionaryValueId: hugeId, value: 'Белый' }],
    })

    const chip = wrapper.findAll('button').find((button) => button.attributes('aria-label') === '移除选项')
    expect(chip).toBeDefined()
    await chip!.trigger('click')
    expect(draft.attributes['85']).toBeUndefined()
  })

  it('Yandex 带单位属性写入 value/unit 结构并支持切换单位', async () => {
    const draft = createEmptyDraftDetail('yandex')
    draft.draftId = 'draft-yandex-unit'
    draft.site = 'global'
    draft.categoryId = '91596'
    const category: CategorySelection = {
      platform: 'yandex',
      categoryId: '91596',
      categoryPath: 'Бытовая техника',
      requiredAttributes: [{
        id: '9048',
        name: 'Вес',
        required: true,
        options: [],
        unitOptions: ['г', 'кг'],
        defaultUnit: 'г',
      }],
      optionalAttributes: [],
      fetchedAt: '2026-08-05T00:00:00Z',
      raw: {},
    }
    const wrapper = mount(CategoryAttributesPanel, {
      props: {
        ...panelProps(draft, category),
        publishTargets: [yandexTarget],
        selectedPublishTarget: yandexTarget,
      },
    })

    const requiredButton = wrapper.findAll('button').find((button) => button.text().startsWith('必填属性'))
    await requiredButton!.trigger('click')

    const input = wrapper.get<HTMLInputElement>('[data-attribute-id="9048"]')
    await input.setValue('500')
    expect(draft.attributes['9048']).toEqual({ value: '500', unit: 'г' })

    const unitSelect = wrapper.get<HTMLSelectElement>('select[aria-label="单位（Вес）"]')
    expect(unitSelect.element.value).toBe('г')
    await unitSelect.setValue('кг')
    expect(draft.attributes['9048']).toEqual({ value: '500', unit: 'кг' })
    expect(wrapper.emitted('invalidateCategoryPrecheck')!.length).toBeGreaterThanOrEqual(2)

    await input.setValue('')
    expect(draft.attributes['9048']).toBeUndefined()
  })
})
