import { describe, expect, it } from 'vitest'
import { createEmptyDraftDetail } from '@/constants/initialState'
import { normalizeDraftDetail, toBackendDraft, toBackendDraftDetail } from '@/api/workflow/normalizers'

describe('类目属性 Schema 映射', () => {
  it('在 Ozon 草稿目标中读写隐藏的 description_category_id', () => {
    const normalized = normalizeDraftDetail({
      draft_id: 'draft-ozon',
      product_id: 'product-ozon',
      platform: 'ozon',
      platforms: ['ozon'],
      site: 'global',
      category_id: '91443',
      description_category_id: '17039635',
      target_sites: [{
        platform: 'ozon',
        site: 'global',
        language: 'ru-RU',
        currency: 'RUB',
        category_id: '91443',
        description_category_id: '17039635',
      }],
    })

    expect(normalized.descriptionCategoryId).toBe('17039635')
    expect(normalized.targetSites[0]?.descriptionCategoryId).toBe('17039635')

    const backend = toBackendDraft(normalized)
    expect(backend.description_category_id).toBe('17039635')
    expect((backend.target_sites as Array<Record<string, unknown>>)[0]?.description_category_id).toBe('17039635')
  })

  it('在目标站点中读写规范化的类目属性定义', () => {
    const normalized = normalizeDraftDetail({
      draft_id: 'draft-1',
      product_id: 'product-1',
      platform: 'mercadolibre',
      platforms: ['mercadolibre'],
      site: 'MLM',
      language: 'es-MX',
      currency: 'MXN',
      target_sites: [{
        platform: 'mercadolibre',
        site: 'MLM',
        language: 'es-MX',
        currency: 'MXN',
        category_id: 'MLM-NEW',
        category_path: '家居 / 新类目',
        category_attribute_schema: {
          version: 1,
          platform: 'mercadolibre',
          site: 'MLM',
          category_id: 'MLM-NEW',
          category_path: '家居 / 新类目',
          source: 'mercadolibre_live',
          fetched_at: '2026-07-25T12:00:00Z',
          required: [{
            id: 'BRAND',
            name: 'Brand',
            required: true,
            options: ['Brand A'],
            value_type: 'list',
          }],
          optional: [],
        },
        attributes: { BRAND: 'Brand A' },
      }],
    })

    expect(normalized.targetSites[0]?.categoryAttributeSchema).toEqual(expect.objectContaining({
      categoryId: 'MLM-NEW',
      source: 'mercadolibre_live',
      required: [expect.objectContaining({
        id: 'BRAND',
        required: true,
        options: ['Brand A'],
        valueType: 'list',
      })],
    }))

    const draft = {
      ...createEmptyDraftDetail('mercadolibre'),
      ...normalized,
      raw: {
        sale_price: '旧价格',
        future_draft_field: '不得回写',
      },
    }
    const backend = toBackendDraft(draft)
    const target = (backend.target_sites as Array<Record<string, unknown>>)[0]
    expect(target.category_attribute_schema).toEqual(expect.objectContaining({
      category_id: 'MLM-NEW',
      fetched_at: '2026-07-25T12:00:00Z',
      required: [expect.objectContaining({
        id: 'BRAND',
        value_type: 'list',
      })],
    }))
    expect(backend).not.toHaveProperty('sale_price')
    expect(target).not.toHaveProperty('publish_logs')

    const backendDetail = toBackendDraftDetail(draft)
    expect(backendDetail).not.toHaveProperty('sale_price')
    expect(backendDetail).not.toHaveProperty('future_draft_field')
  })

  it('不会从旧 camelCase 草稿字段静默回捞数据', () => {
    const normalized = normalizeDraftDetail({
      draftId: 'legacy-draft',
      productId: 'legacy-product',
      platform: 'mercadolibre',
      site_id: 'MLM',
      categoryId: 'MLM-OLD',
      sale_price: '999',
      packageDimensions: {
        lengthCm: '10',
      },
    })

    expect(normalized.draftId).toBe('')
    expect(normalized.productId).toBe('')
    expect(normalized.categoryId).toBe('')
    expect(normalized.pricing).toEqual({})
    expect(normalized.packageDimensions.lengthCm).toBe('')
    expect(normalized.targetSites[0]?.categoryAttributeSchema).toBeNull()
  })

  it('目标站点明确清空后不会恢复草稿根级属性和待复核提示', () => {
    const normalized = normalizeDraftDetail({
      draft_id: 'draft-1',
      product_id: 'product-1',
      platform: 'mercadolibre',
      platforms: ['mercadolibre'],
      site: 'MLM',
      category_id: 'MLM-OLD',
      attributes: { OLD_ATTRIBUTE: '旧值' },
      validation_errors: [{
        code: 'NEED_REVIEW_ATTRIBUTES',
        field: 'attributes.OLD_ATTRIBUTE',
      }],
      publish_status: 'ready',
      target_sites: [{
        platform: 'mercadolibre',
        site: 'MLM',
        language: 'es-MX',
        currency: 'MXN',
        category_id: 'MLM-NEW',
        category_path: '新类目',
        category_attribute_schema: {},
        attributes: {},
        validation_errors: [],
        category_precheck: {},
        publish_status: '',
      }],
    })

    expect(normalized.targetSites[0]).toEqual(expect.objectContaining({
      categoryId: 'MLM-NEW',
      categoryPath: '新类目',
      categoryAttributeSchema: null,
      attributes: {},
      validationErrors: [],
      categoryPrecheck: {},
      publishStatus: '',
    }))
  })

  it('保留 Ozon 字典元数据和选中的 dictionary_value_id', () => {
    const normalized = normalizeDraftDetail({
      draft_id: 'draft-ozon-dictionary',
      product_id: 'product-ozon-dictionary',
      platform: 'ozon',
      platforms: ['ozon'],
      site: 'global',
      category_id: '94765',
      target_sites: [{
        platform: 'ozon',
        site: 'global',
        language: 'ru-RU',
        listing_currency: 'RUB',
        category_id: '94765',
        category_attribute_schema: {
          version: 1,
          platform: 'ozon',
          site: 'global',
          category_id: '94765',
          required: [{
            id: '85',
            name: 'Бренд',
            required: true,
            dictionary_id: '28732849',
            is_dictionary: true,
            category_dependent: true,
          }],
          optional: [],
        },
        attributes: {
          85: {
            values: [{ dictionary_value_id: 126745801, value: 'Нет бренда' }],
          },
        },
      }],
    })

    expect(normalized.targetSites[0]?.categoryAttributeSchema?.required[0]).toEqual(expect.objectContaining({
      dictionaryId: '28732849',
      isDictionary: true,
      categoryDependent: true,
    }))
    expect(normalized.targetSites[0]?.attributes?.['85']).toEqual({
      values: [{ dictionaryValueId: 126745801, value: 'Нет бренда' }],
    })

    const backend = toBackendDraft(normalized)
    const target = (backend.target_sites as Array<Record<string, unknown>>)[0] as Record<string, unknown>
    expect((target.attributes as Record<string, unknown>)['85']).toEqual({
      values: [{ dictionary_value_id: 126745801, value: 'Нет бренда' }],
    })
  })

  it('把 Ozon dictionary_id=0 规范化为普通文本属性', () => {
    const normalized = normalizeDraftDetail({
      draft_id: 'draft-ozon-free-text',
      product_id: 'product-ozon-free-text',
      platform: 'ozon',
      platforms: ['ozon'],
      site: 'global',
      category_id: '91443',
      target_sites: [{
        platform: 'ozon',
        site: 'global',
        category_id: '91443',
        category_attribute_schema: {
          version: 2,
          platform: 'ozon',
          site: 'global',
          category_id: '91443',
          required: [{
            id: '9048',
            name: 'Название модели',
            required: true,
            dictionary_id: '0',
            is_dictionary: true,
          }],
          optional: [],
        },
        attributes: { 9048: 'F30' },
      }],
    })

    const attribute = normalized.targetSites[0]?.categoryAttributeSchema?.required[0]
    expect(attribute?.dictionaryId).toBe('')
    expect(attribute?.isDictionary).toBe(false)

    const backend = toBackendDraft(normalized)
    const target = (backend.target_sites as Array<Record<string, unknown>>)[0]
    const backendAttribute = (target.category_attribute_schema as Record<string, unknown>).required as Array<Record<string, unknown>>
    expect(backendAttribute[0]).toEqual(expect.objectContaining({
      dictionary_id: '',
      is_dictionary: false,
    }))
  })
})
