import { describe, expect, it } from 'vitest'
import { createEmptyDraftDetail, createEmptyProduct } from '@/constants/initialState'
import { normalizeDraftDetail, toBackendDraft, toBackendDraftDetail, toBackendProduct } from '@/api/workflow/normalizers'
import type { DraftDetail } from '@/types/workflow'

describe('类目 Schema 分离（废弃字段不再读写）', () => {
  it('在目标站点中往返保留发布任务快照', () => {
    const normalized = normalizeDraftDetail({
      draft_id: 'draft-publish-task',
      product_id: 'product-publish-task',
      platform: 'ozon',
      platforms: ['ozon'],
      site: 'global',
      target_sites: [{
        platform: 'ozon',
        site: 'global',
        language: 'ru-RU',
        last_publish_task: {
          job_id: 'job-publish-task',
          status: 'published',
        },
      }],
    })

    expect(normalized.targetSites[0]?.lastPublishTask).toEqual({
      job_id: 'job-publish-task',
      status: 'published',
    })
    const backend = toBackendDraft(normalized)
    expect((backend.target_sites as Array<Record<string, unknown>>)[0]?.last_publish_task).toEqual({
      job_id: 'job-publish-task',
      status: 'published',
    })
  })

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

  it('忽略历史 category_attribute_schema，保存 payload 不再包含该字段', () => {
    const normalized = normalizeDraftDetail({
      draft_id: 'draft-1',
      product_id: 'product-1',
      platform: 'mercadolibre',
      platforms: ['mercadolibre'],
      site: 'MLM',
      language: 'es-MX',
      currency: 'MXN',
      category_attribute_schema: {
        platform: 'mercadolibre',
        category_id: 'MLM-ROOT',
      },
      target_sites: [{
        platform: 'mercadolibre',
        site: 'MLM',
        language: 'es-MX',
        currency: 'MXN',
        category_id: 'MLM-NEW',
        category_path: '家居 / 新类目',
        category_attribute_schema: {
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

    // 历史 schema 读取时被忽略：编辑态定义改由 /api/category-attrs 瞬时加载。
    expect(normalized.targetSites[0]).not.toHaveProperty('categoryAttributeSchema')
    expect(normalized.targetSites[0]?.categoryId).toBe('MLM-NEW')

    const draft: DraftDetail = {
      ...createEmptyDraftDetail('mercadolibre'),
      ...normalized,
      raw: {
        sale_price: '旧价格',
        future_draft_field: '不得回写',
      },
    }
    const backend = toBackendDraft(draft)
    const target = (backend.target_sites as Array<Record<string, unknown>>)[0]
    expect(backend).not.toHaveProperty('category_attribute_schema')
    expect(target).not.toHaveProperty('category_attribute_schema')
    expect(target?.attributes).toEqual({ BRAND: 'Brand A' })
    expect(backend).not.toHaveProperty('sale_price')
    expect(target).not.toHaveProperty('publish_logs')

    const backendDetail = toBackendDraftDetail(draft)
    expect(backendDetail).not.toHaveProperty('category_attribute_schema')
    expect((backendDetail.target_sites as Array<Record<string, unknown>>)[0]).not.toHaveProperty('category_attribute_schema')
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
    expect(normalized.targetSites[0]).not.toHaveProperty('categoryAttributeSchema')
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
      attributes: {},
      validationErrors: [],
      categoryPrecheck: {},
      publishStatus: '',
    }))
    expect(normalized.targetSites[0]).not.toHaveProperty('categoryAttributeSchema')
  })

  it('保留 Ozon 选中的 dictionary_value_id，且不携带废弃 schema', () => {
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

    expect(normalized.targetSites[0]).not.toHaveProperty('categoryAttributeSchema')
    expect(normalized.targetSites[0]?.attributes?.['85']).toEqual({
      values: [{ dictionaryValueId: '126745801', value: 'Нет бренда' }],
    })

    const backend = toBackendDraft(normalized)
    const target = (backend.target_sites as Array<Record<string, unknown>>)[0] as Record<string, unknown>
    expect((target.attributes as Record<string, unknown>)['85']).toEqual({
      values: [{ dictionary_value_id: '126745801', value: 'Нет бренда' }],
    })
    expect(target).not.toHaveProperty('category_attribute_schema')
    expect(backend).not.toHaveProperty('category_attribute_schema')
  })

  it('往返保留开放集合中的平台候选与无 ID 自定义值', () => {
    const normalized = normalizeDraftDetail({
      draft_id: 'draft-mercado-collection',
      product_id: 'product-mercado-collection',
      platform: 'mercadolibre',
      platforms: ['mercadolibre'],
      site: 'CBT',
      category_id: 'CBT455865',
      attributes: {
        COMPATIBLE_DEVICES: {
          values: [
            { dictionary_value_id: '123', value: 'Phone' },
            { value: 'Custom terminal' },
          ],
        },
      },
    })

    expect(normalized.attributes.COMPATIBLE_DEVICES).toEqual({
      values: [
        { dictionaryValueId: '123', value: 'Phone' },
        { value: 'Custom terminal' },
      ],
    })

    const backend = toBackendDraft(normalized)
    expect((backend.attributes as Record<string, unknown>).COMPATIBLE_DEVICES).toEqual({
      values: [
        { dictionary_value_id: '123', value: 'Phone' },
        { value: 'Custom terminal' },
      ],
    })
  })

  it('保存商品时不回写 local_platform_categories', () => {
    const product = createEmptyProduct()
    product.productId = 'product-1'
    product.raw = {
      local_platform_categories: { ozon: { category_id: '91443' } },
      localPlatformCategories: { ozon: { category_id: '91443' } },
    }

    const backend = toBackendProduct(product)
    expect(backend).not.toHaveProperty('local_platform_categories')
    expect(backend).not.toHaveProperty('localPlatformCategories')
  })
})
