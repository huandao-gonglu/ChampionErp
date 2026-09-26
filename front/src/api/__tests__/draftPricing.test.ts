import { beforeEach, describe, expect, it, vi } from 'vitest'
import { apiClient } from '@/api/client'
import { draftPricingPayload, priceDraft } from '@/api/workflow/publishing'
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
    commissionPercent: 20, paymentFeePercent: 0, otherFeePercent: 0, pricingMode: 'margin', targetMarginPercent: 30,
    markupPercent: 30, shippingQuoteMode: 'auto', shippingCurrency: 'CNY', shippingAmount: 0, manualPrice: null }]
  return { draft, input }
}

describe('统一草稿核价 API', () => {
  beforeEach(() => vi.clearAllMocks())

  it('198 个 SKU 只提交费用与编辑值，不从前端展开采购成本和包装事实', async () => {
    const { draft, input } = fixture()
    vi.mocked(apiClient.post).mockResolvedValueOnce({ data: { ok: true, applied: false, errors: [],
      items: [...draft.skuItems].reverse().map((row, index) => ({ sku_id: row.sku_id, result: { ok: true, results: [{
        target_key: 'ozon:global', platform: 'ozon', site: 'global', listing_currency: 'CNY',
        applied_price: { amount: String(index + 1), currency: 'CNY' }, errors: [],
      }] } })), sku_pricing: {}, metrics: {} } })
    const result = await priceDraft(draft, input)
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

  it('保留尚未保存的成本覆盖、尺寸覆盖、固定费用和国际运费覆盖', () => {
    const { draft, input } = fixture(1)
    draft.skuItems[0]!.overrides = { name: '未保存的名称', cost_cny: '15', package_dimensions: { weight_kg: '0.8' } }
    draft.skuItems[0]!.pricing_overrides = { common: { domestic_freight_cny: 8 }, targets: { 'ozon:global': { shipping_amount: 12 } } }
    const payload = draftPricingPayload(draft, input)
    expect(payload.sku_updates).toEqual([expect.objectContaining({
      overrides: { cost_cny: '15', package_dimensions: { weight_kg: '0.8' } },
      pricing_overrides: draft.skuItems[0]!.pricing_overrides,
    })])
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
    const result = await priceDraft(draft, input, true)
    expect(apiClient.post).toHaveBeenCalledWith('/api/draft-pricing/apply', expect.anything(), { timeout: 0 })
    expect(result.applied).toBe(false)
    expect(result.errors[0]?.message).toBe('物流不可用')
  })
})
