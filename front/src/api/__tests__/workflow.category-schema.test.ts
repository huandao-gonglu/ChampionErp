import { describe, expect, it } from 'vitest'
import { createEmptyDraftDetail } from '@/constants/initialState'
import { normalizeDraftDetail, toBackendDraft } from '@/api/workflow/normalizers'

describe('类目属性 Schema 映射', () => {
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
  })

  it('兼容没有类目属性 Schema 的旧草稿', () => {
    const normalized = normalizeDraftDetail({
      draft_id: 'legacy-draft',
      product_id: 'product-1',
      platform: 'mercadolibre',
      site: 'MLM',
      category_id: 'MLM-OLD',
      attributes: { BRAND: 'Legacy Brand' },
    })

    expect(normalized.targetSites[0]?.categoryAttributeSchema).toBeNull()
    expect(normalized.targetSites[0]?.attributes).toEqual({ BRAND: 'Legacy Brand' })
  })

  it('目标站点明确清空后不会恢复草稿根级的旧属性和待复核提示', () => {
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
})
