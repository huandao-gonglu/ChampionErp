import { describe, expect, it } from 'vitest'
import { normalizeDraftDetail, toBackendDraft, toBackendDraftDetail } from '@/api/workflow/normalizers'

describe('类目 Schema 分离（废弃字段不再读写）', () => {
  it('逐 SKU 平台枚举 ID 与单位保存后原样往返，保留大整数 ID', () => {
    const own = { color: { values: [{ dictionary_value_id: '9007199254740993123', value: 'Черный' }] }, size: { value: '20', unit: 'cm' } }
    const normalized = normalizeDraftDetail({ draft_id: 'sku-attributes', platform: 'ozon', site: 'global', sku_items: [{
      sku_id: 'mask-black', selected: true, sku: 'BLACK', stock: '0', overrides: {}, pricing: {}, publications: {},
      attributes_by_target: { 'ozon:global': own, 'yandex:global': { color: '暗夜黑' } },
    }] })
    expect(normalized.skuItems[0]?.attributes_by_target['ozon:global']?.color).toEqual({ values: [{ dictionaryValueId: '9007199254740993123', value: 'Черный' }] })
    const saved = toBackendDraftDetail(normalized)
    expect((saved.sku_items as Array<Record<string, unknown>>)[0]?.attributes_by_target).toEqual({ 'ozon:global': own, 'yandex:global': { color: '暗夜黑' } })
  })

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
})
