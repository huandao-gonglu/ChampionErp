import { describe, expect, it } from 'vitest'
import {
  normalizeDraftsIndex,
  normalizeProductsIndex,
  normalizePublishLogs,
} from '@/api/workflow/normalizers'

describe('workflow 当前 wire schema', () => {
  it('索引只读取当前 snake_case 字段', () => {
    const [product] = normalizeProductsIndex([{
      product_id: 'product-current',
      productId: 'product-legacy',
      id: 'product-legacy-id',
      title: '当前标题',
      name: '旧标题',
      main_image: '/current.jpg',
      mainImage: '/legacy.jpg',
      source_platform: '1688',
      sourcePlatform: 'legacy',
      workflow_status: 'images_ready',
      workflowStatus: 'published',
      draft_statuses: { mercadolibre: 'images_ready' },
      draftStatuses: { mercadolibre: 'published' },
    }])
    const [draft] = normalizeDraftsIndex([{
      draft_id: 'draft-current',
      draftId: 'draft-legacy',
      product_id: 'product-current',
      productId: 'product-legacy',
      source_product_id: 'source-current',
      sourceProductId: 'source-legacy',
      platform: 'mercadolibre',
      platforms: ['mercadolibre'],
      site: 'MLM',
      target_sites: [{
        platform: 'mercadolibre',
        site: 'MLM',
        language: 'es-MX',
        currency: 'MXN',
        category_id: 'MLM-CURRENT',
      }],
      targetSites: [{
        platform: 'mercadolibre',
        site: 'MLM',
        category_id: 'MLM-LEGACY',
      }],
      product_title: '当前商品标题',
      productTitle: '旧商品标题',
      main_image: '/draft-current.jpg',
      mainImage: '/draft-legacy.jpg',
    }])

    expect(product).toEqual(expect.objectContaining({
      productId: 'product-current',
      title: '当前标题',
      mainImage: '/current.jpg',
      sourcePlatform: '1688',
      workflowStatus: 'images_ready',
      draftStatuses: { mercadolibre: 'images_ready' },
    }))
    expect(draft).toEqual(expect.objectContaining({
      draftId: 'draft-current',
      productId: 'product-current',
      sourceProductId: 'source-current',
      productTitle: '当前商品标题',
      mainImage: '/draft-current.jpg',
    }))
    expect(draft?.targetSites[0]?.categoryId).toBe('MLM-CURRENT')
  })

  it('发布日志保留当前日志变体，但不再读取 camelCase 别名', () => {
    const [log] = normalizePublishLogs([{
      job_id: 'job-current',
      jobId: 'job-legacy',
      product_id: 'product-current',
      productId: 'product-legacy',
      platform: 'mercadolibre',
      started_at: '',
      time: '2026-07-30 10:00:00',
      error_message: '',
      error: '当前日志错误详情',
      request_payload_path: '/current/request.json',
      requestPayloadPath: '/legacy/request.json',
    }])

    expect(log).toEqual(expect.objectContaining({
      jobId: 'job-current',
      productId: 'product-current',
      startedAt: '2026-07-30 10:00:00',
      errorMessage: '当前日志错误详情',
      requestPayloadPath: '/current/request.json',
    }))
  })
})
