import { ref, watch, nextTick } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createWorkflowPricingActions } from '@/stores/workflow/actions/pricing'
import { priceDraft, type DraftPricingBatch } from '@/api/workflow/publishing'
import { createDefaultPricingInput, createEmptyDraftDetail, createEmptyProduct } from '@/constants/initialState'

vi.mock('@/api/workflow/publishing', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/workflow/publishing')>()), priceDraft: vi.fn(),
}))

function setup() {
  const draft = createEmptyDraftDetail()
  draft.draftId = 'draft-1'
  draft.productId = 'product-1'
  draft.updatedAt = 'version-1'
  draft.title = '未保存的标题'
  draft.skuItems = [{ sku_id: 'sku-1', sku: 'SELL-1', selected: true, stock: '5', overrides: { name: '未保存的名称' },
    pricing: {}, pricing_overrides: {}, attributes_by_target: {}, publications: {} }]
  const input = createDefaultPricingInput()
  input.domesticFreightCny = 20
  input.targets = [{ targetKey: 'ozon:global', platform: 'ozon', site: 'global', listingCurrency: 'CNY', sitesToSell: [],
    commissionPercent: 20, paymentFeePercent: 0, otherFeePercent: 0, pricingMode: 'margin', targetMarginPercent: 30,
    markupPercent: 30, shippingQuoteMode: 'auto', shippingCurrency: 'CNY', shippingAmount: 0, manualPrice: null }]
  const runtime = {
    product: ref(createEmptyProduct()), currentDraft: ref(draft), pricingInput: ref(input), pricingResult: ref(null),
    storeConfig: ref({}), loading: ref(false), currentStage: ref(0), addLog: vi.fn(), setError: vi.fn(), applyMutationIndexes: vi.fn(),
  }
  const actions = createWorkflowPricingActions(runtime as unknown as Parameters<typeof createWorkflowPricingActions>[0])
  const response: DraftPricingBatch = { items: [], applied: false, errors: [], pricingBySku: { 'sku-1': { applied: false, targets: {} } },
    metrics: { batchId: 'batch', durationMs: 12, ozonDiscoveryMs: 0 } }
  return { runtime, actions, response }
}

describe('页面核价使用后端业务入口', () => {
  beforeEach(() => vi.clearAllMocks())

  it('没有在页面读取过 SKU 采购成本也能预览，预览不应用', async () => {
    const { runtime, actions, response } = setup()
    vi.mocked(priceDraft).mockResolvedValue(response)
    await actions.calculatePrice()
    expect(priceDraft).toHaveBeenCalledWith(expect.objectContaining({ draftId: 'draft-1' }),
      expect.objectContaining({ domesticFreightCny: 20 }), false)
    expect(runtime.currentDraft.value.updatedAt).toBe('version-1')
    expect(runtime.currentDraft.value.skuItems[0]?.pricing.applied).toBe(false)
    expect(runtime.loading.value).toBe(false)
  })

  it('应用只合并核价结果，保留其他尚未保存的页面编辑', async () => {
    const { runtime, actions, response } = setup()
    const saved = structuredClone(JSON.parse(JSON.stringify(runtime.currentDraft.value)))
    saved.title = '后端原有标题'
    saved.updatedAt = 'version-2'
    saved.pricing = { common: { domestic_freight_cny: 20 } }
    vi.mocked(priceDraft).mockResolvedValue({ ...response, applied: true, draft: saved,
      pricingBySku: { 'sku-1': { applied: true, targets: { 'ozon:global': { applied_price: { amount: '100', currency: 'CNY' } } } } } })
    await actions.applyPrice()
    expect(runtime.currentDraft.value.title).toBe('未保存的标题')
    expect(runtime.currentDraft.value.skuItems[0]?.overrides.name).toBe('未保存的名称')
    expect(runtime.currentDraft.value.updatedAt).toBe('version-2')
    expect(runtime.currentDraft.value.skuItems[0]?.pricing.applied).toBe(true)
  })

  it('计算过程中更改参数时丢弃旧预览，保留新的输入', async () => {
    const { runtime, actions, response } = setup()
    vi.mocked(priceDraft).mockImplementation(async () => {
      runtime.pricingInput.value.domesticFreightCny = 25
      return response
    })
    await actions.calculatePrice()
    expect(runtime.setError).toHaveBeenLastCalledWith(expect.stringContaining('已改变'))
    expect(runtime.currentDraft.value.skuItems[0]?.pricing).toEqual({})
    expect(runtime.pricingInput.value.domesticFreightCny).toBe(25)
  })

  it('只显示系统实际返回的问题，并保留 SKU 归属', async () => {
    const { runtime, actions, response } = setup()
    vi.mocked(priceDraft).mockResolvedValue({ ...response, errors: [{ sku_id: 'sku-1', message: '缺少包装重量' }] })
    await actions.applyPrice()
    expect(runtime.setError).toHaveBeenLastCalledWith('核价需要处理：sku-1：缺少包装重量')
    expect(runtime.currentDraft.value.updatedAt).toBe('version-1')
  })

  it('应用期间新增编辑保留，并使用服务端新版本继续核价', async () => {
    const { runtime, actions, response } = setup()
    const saved = JSON.parse(JSON.stringify(runtime.currentDraft.value))
    saved.updatedAt = 'version-2'
    vi.mocked(priceDraft).mockImplementationOnce(async () => {
      runtime.pricingInput.value.domesticFreightCny = 25
      return { ...response, applied: true, draft: saved }
    })
    await actions.applyPrice()
    expect(runtime.setError).toHaveBeenLastCalledWith(expect.stringContaining('新修改已保留'))
    expect(runtime.currentDraft.value.updatedAt).toBe('version-2')
    expect(runtime.pricingInput.value.domesticFreightCny).toBe(25)
    expect(runtime.currentDraft.value.skuItems[0]?.pricing.applied).toBe(false)
  })

  it('系统归一化费用覆盖触发表单 watcher 后，应用结果仍保持有效', async () => {
    const { runtime, actions, response } = setup()
    const stop = watch(() => JSON.stringify(runtime.currentDraft.value.skuItems.map(row => row.pricing_overrides)), () => {
      for (const row of runtime.currentDraft.value.skuItems) row.pricing.applied = false
    })
    const saved = JSON.parse(JSON.stringify(runtime.currentDraft.value))
    saved.skuItems[0].pricing_overrides = { targets: { 'ozon:global': { shipping_amount: 12 } } }
    vi.mocked(priceDraft).mockResolvedValue({ ...response, applied: true, draft: saved,
      pricingBySku: { 'sku-1': { applied: true, targets: {} } } })
    await actions.applyPrice()
    await nextTick()
    expect(runtime.currentDraft.value.skuItems[0]?.pricing.applied).toBe(true)
    stop()
  })
})
