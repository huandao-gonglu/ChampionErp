import { beforeEach, describe, expect, it, vi } from 'vitest'
import { apiClient } from '@/api/client'
import { draftPricingPayload, draftWithPricingInput, priceDraft, restoreDraftPricing } from '@/api/workflow/publishing'
import { createDefaultPricingInput, createEmptyDraftDetail } from '@/constants/initialState'

vi.mock('@/api/client', () => ({ API_REQUEST_TIMEOUT_MS: 30000, apiClient: { post: vi.fn() } }))

function fixture(count = 198) {
  const draft = createEmptyDraftDetail()
  draft.draftId = 'draft-1'
  draft.updatedAt = 'version-1'
  draft.skuItems = Array.from({ length: count }, (_, index) => ({ sku_id: `sku-${index}`, sku: `SELL-${index}`,
    selected: true, stock: '5', overrides: {}, pricing_overrides: {}, pricing: {}, attributes_by_target: {}, publications: {} }))
  const input = createDefaultPricingInput()
  input.domesticFreightCny = 20
  input.targets = [{ targetKey: 'ozon:global', platform: 'ozon', site: 'global', listingCurrency: 'CNY', sitesToSell: [],
    paymentFeePercent: 0, otherFeePercent: 0, pricingMode: 'margin', targetMarginPercent: 30,
    markupPercent: 30, shippingQuoteMode: 'auto', shippingCurrency: 'CNY', shippingAmount: 0, manualPrice: null }]
  return { draft, input }
}

describe('统一草稿核价 API', () => {
  beforeEach(() => vi.clearAllMocks())

  it('返回时只查询已有预览并恢复应用标识，不调用计算接口', async () => {
    const { draft, input } = fixture(1)
    vi.mocked(apiClient.post).mockResolvedValueOnce({ data: {
      preview_id: 'previous-preview', applied: false, errors: [],
      items: [{ sku_id: 'sku-0', result: { results: [{
        target_key: 'ozon:global', platform: 'ozon', site: 'global', listing_currency: 'CNY',
        applied_price: { amount: '120', currency: 'CNY' },
      }] } }], sku_pricing: { 'sku-0': { applied: false, targets: {} } },
    } })
    const restored = await restoreDraftPricing(draft, input)
    expect(apiClient.post).toHaveBeenCalledTimes(1)
    expect(apiClient.post).toHaveBeenCalledWith('/api/draft-pricing/restore', draftPricingPayload(draft, input))
    expect(restored?.items[0]?.result.previewId).toBe('previous-preview')
    expect(restored?.items[0]?.result.results[0]?.appliedPrice.amount).toBe('120')
    expect(restored?.pricingBySku['sku-0']?.applied).toBe(false)
  })

  it('没有有效预览时返回空，不将缺少 SKU 误判为核价响应错误', async () => {
    const { draft, input } = fixture(1)
    vi.mocked(apiClient.post).mockResolvedValueOnce({ data: { preview_id: '', applied: false } })
    expect(await restoreDraftPricing(draft, input)).toBeNull()
    expect(apiClient.post).toHaveBeenCalledTimes(1)
  })

  it('模板来源随核价参数提交，未选择时显式为空', () => {
    const { draft, input } = fixture(1)
    expect(draftPricingPayload(draft, input).template_source).toBeNull()
    input.templateSource = { id: 'used', name: '日常费用' }
    expect(draftPricingPayload(draft, input).template_source).toEqual({ id: 'used', name: '日常费用' })
  })

  it('普通保存合并参数且保留已有报价，提交前不修改当前草稿', () => {
    const { draft, input } = fixture(1)
    draft.pricing = {
      template_source: { id: 'old', name: '旧模板' },
      common: { domestic_freight_cny: 3 },
      targets: {
        'ozon:global': { calculation_fingerprint: 'saved-quote', target_margin_percent: 10 },
        'yandex:global': { target_margin_percent: 40 },
      },
    }
    draft.skuItems[0]!.pricing = { applied: false, targets: {} }
    input.templateSource = { id: 'new', name: '新模板' }
    const before = JSON.stringify(draft)
    const saved = draftWithPricingInput(draft, input, createDefaultPricingInput())
    expect(saved.pricing).toMatchObject({
      template_source: { id: 'new', name: '新模板' },
      common: { domestic_freight_cny: 20 },
      targets: {
        'ozon:global': { calculation_fingerprint: 'saved-quote', target_margin_percent: 30 },
        'yandex:global': { target_margin_percent: 40 },
      },
    })
    expect(saved.skuItems[0]!.pricing.applied).toBe(false)
    expect(JSON.stringify(draft)).toBe(before)
  })

  it('货损为空时发送零值停用，共用和 SKU 覆盖分别传递', () => {
    const { draft, input } = fixture(1)
    Object.assign(input.targets[0], { returnRatePercent: 10, returnDisposition: 'resell', returnHandlingCostCny: 40 })
    draft.skuItems[0].pricing_overrides = { targets: { 'ozon:global': { return_rate_percent: 0 } } }
    expect(draftPricingPayload(draft, input)).toMatchObject({ targets: { 'ozon:global': { return_rate_percent: 10, return_disposition: 'resell', return_handling_cost_cny: 40 } }, sku_updates: [{ pricing_overrides: { targets: { 'ozon:global': { return_rate_percent: 0 } } } }] })
    input.targets[0].returnRatePercent = ''
    expect(draftPricingPayload(draft, input)).toMatchObject({ targets: { 'ozon:global': { return_rate_percent: 0 } } })
  })

  it('传递指定仓库物流并通过显式 null 恢复比价', () => {
    const { draft, input } = fixture(1)
    Object.assign(input.targets[0], { platform: 'yandex', targetKey: 'yandex:global', shippingSelection: { warehouse_id: 31, route_id: 'route-1' } })
    expect(draftPricingPayload(draft, input)).toMatchObject({
      targets: { 'yandex:global': { shipping_selection: { warehouse_id: 31, route_id: 'route-1' } } },
    })
    input.targets[0].shippingSelection = null
    expect(draftPricingPayload(draft, input)).toMatchObject({
      targets: { 'yandex:global': { shipping_selection: null } },
    })
  })

  it('198 个 SKU 只提交费用与编辑值，不从前端展开采购成本和包装事实', async () => {
    const { draft, input } = fixture()
    vi.mocked(apiClient.post).mockResolvedValueOnce({ data: { ok: true, preview_id: 'verified-preview', applied: false, errors: [],
      items: [...draft.skuItems].reverse().map((row, index) => ({ sku_id: row.sku_id, result: { ok: true, results: [{
        target_key: 'ozon:global', platform: 'ozon', site: 'global', listing_currency: 'CNY',
        applied_price: { amount: String(index + 1), currency: 'CNY' }, errors: [],
      }] } })), sku_pricing: {}, metrics: {} } })
    const result = await priceDraft(draft, input)
    expect(result.previewId).toBe('verified-preview')
    expect(result.items[0]?.result.previewId).toBe('verified-preview')
    expect(result.items).toHaveLength(198)
    expect(result.items[0]?.skuId).toBe('sku-197')
    expect(result.items[0]?.result.results[0]?.appliedPrice.amount).toBe('1')
    expect(apiClient.post).toHaveBeenCalledWith('/api/draft-pricing/preview', expect.objectContaining({
      draft_id: 'draft-1', expected_updated_at: 'version-1',
      common: expect.objectContaining({ domestic_freight_cny: 20 }),
      targets: { 'ozon:global': expect.objectContaining({ shipping_quote_mode: 'auto', shipping_amount: 0 }) },
    }), { timeout: 0 })
    const payload = draftPricingPayload(draft, input)
    expect(JSON.stringify(payload)).not.toContain('purchase_cost')
    expect(JSON.stringify(payload)).not.toContain('weight_kg')
  })

  it('仅提交费用覆盖，忽略旧页面中残留的来源覆盖', () => {
    const { draft, input } = fixture(1)
    draft.skuItems[0]!.overrides = { name: '未保存的名称', cost_cny: '15', package_dimensions: { weight_kg: '0.8' } }
    draft.skuItems[0]!.pricing_overrides = { common: { domestic_freight_cny: 8 }, targets: { 'ozon:global': { shipping_amount: 12 } } }
    const payload = draftPricingPayload(draft, input)
    expect(payload.sku_updates).toEqual([expect.objectContaining({

      pricing_overrides: draft.skuItems[0]!.pricing_overrides,
    })])
    expect(JSON.stringify(payload.sku_updates)).not.toContain('overrides":{"cost_cny')
  })

  it('仅提交本次选择的市场和汇率覆盖，保留未选市场规则', () => {
    const { draft, input } = fixture(1)
    input.targets.push({ ...input.targets[0], targetKey: 'mercadolibre:cbt', platform: 'mercadolibre', site: 'CBT' })
    draft.targetSites.push({ platform: 'mercadolibre', site: 'CBT', language: 'es-MX', listingCurrency: 'USD', sitesToSell: [{ siteId: 'MLM', logisticType: 'remote' }] })
    input.selectedTargetKeys = ['ozon:global']
    input.exchangeRateOverrides = { usd_cny_rate: 7.05 }
    const payload = draftPricingPayload(draft, input)
    expect(payload.target_keys).toEqual(['ozon:global'])
    expect(Object.keys(payload.targets as object)).toEqual(['ozon:global'])
    expect(payload.target_selections).toEqual({})
    expect(payload.common).toMatchObject({ exchange_rate_mode: 'live', exchange_rate_overrides: { usd_cny_rate: 7.05 } })
    expect(input.targets).toHaveLength(2)
  })

  it.each([{ ids: [] }, { ids: ['sku-0', 'sku-0'] }, { ids: ['sku-0', 'unknown'] }])('拒绝不完整或重复的 SKU 返回：$ids', async ({ ids }) => {
    const { draft, input } = fixture(2)
    vi.mocked(apiClient.post).mockResolvedValueOnce({ data: { ok: true, items: ids.map(sku_id => ({ sku_id, result: {} })) } })
    await expect(priceDraft(draft, input)).rejects.toThrow('SKU 与本次选择不一致')
  })

  it('应用使用独立端点并保留逐 SKU 系统错误，不触发普通草稿保存', async () => {
    const { draft, input } = fixture(1)
    vi.mocked(apiClient.post).mockResolvedValueOnce({ data: { ok: true, applied: false,
      items: [{ sku_id: 'sku-0', result: { ok: false, error: '物流不可用' } }],
      errors: [{ sku_id: 'sku-0', target_key: 'ozon:global', message: '物流不可用' }], metrics: {} } })
    const result = await priceDraft(draft, input, true, 'verified-preview')
    expect(apiClient.post).toHaveBeenCalledWith('/api/draft-pricing/apply', expect.objectContaining({ preview_id: 'verified-preview' }), { timeout: 0 })
    expect(result.applied).toBe(false)
    expect(result.errors[0]?.message).toBe('物流不可用')
  })
})
