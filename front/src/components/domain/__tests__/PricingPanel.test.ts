// @vitest-environment jsdom

import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { reactive } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { useAiChatStore } from '@/stores/aiChat'
import { setOperationAvailability } from '@/utils/desktop'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import PricingPanel from '@/components/domain/PricingPanel.vue'
import { pricingTemplateParameters } from '@/types/pricing'
import { buildSkuPricingEntries, pricingRecord } from '@/utils/skuPricing'
import { createEmptyDraftProductContext } from '@/constants/initialState'
import type { DraftSku, PricingInput, PricingResult, ProductSku, UnknownRecord } from '@/types/workflow'

const input: PricingInput = {
  platform: 'mercadolibre',
  site: 'CBT',
  purchaseCostCny: 100,
  domesticFreightCny: 0,
  packagingCostCny: 0,
  otherCostCny: 0,
  weightKg: 1,
  lengthCm: 10,
  widthCm: 10,
  heightCm: 10,
  usdCnyRate: 7,
  mxnUsdRate: 17,
  rubCnyRate: 12,
  exchangeRateMode: 'live',
  targets: [{
    targetKey: 'mercadolibre:cbt',
    platform: 'mercadolibre',
    site: 'CBT',
    sitesToSell: [{ siteId: 'MLM', logisticType: 'remote' }],
    listingCurrency: 'USD',
    paymentFeePercent: 0,
    otherFeePercent: 0,
    pricingMode: 'margin',
    targetMarginPercent: 30,
    markupPercent: 30,
    shippingQuoteMode: 'auto',
    shippingCurrency: 'USD',
    shippingAmount: 0,
    manualPrice: null,
  }],
}

const result: PricingResult = {
  results: [{
    targetKey: 'mercadolibre:cbt',
    platform: 'mercadolibre',
    site: 'CBT',
    listingCurrency: 'USD',
    suggestedPrice: { amount: '167.67', currency: 'USD' },
    appliedPrice: { amount: '167.67', currency: 'USD' },
    appliedNetProceeds: { amount: '57.71', currency: 'USD' },
    destinationResults: [{
      siteId: 'MLM',
      logisticType: 'remote',
      pricingModel: 'net_proceeds',
      price: null,
      netProceeds: { amount: '57.71', currency: 'USD' },
    }],
    convertedPrices: {},
    calculationBasis: {},
    calculationFingerprint: 'fingerprint',
    shippingCostUsd: 83.13,
    shippingCostCny: 581.91,
    totalCostCny: 700,
    netRevenueCny: 403.97,
    profitCny: 100,
    marginPercent: 30,
    commissionPercent: 16,
    paymentFeePercent: 0,
    otherFeePercent: 0,
    pricingMode: 'margin',
    targetMarginPercent: 30,
    markupPercent: 30,
    shippingQuoteMode: 'auto',
    shippingCurrency: 'USD',
    shippingAmount: 83.13,
    shippingSource: 'international_shipping',
    commissionCny: 187.81,
    paymentFeeCny: 0,
    otherFeeCny: 0,
    minimumPrice: { amount: '150.00', currency: 'USD' },
    billableWeightKg: 1,
    usdCnyRate: 7,
    mxnUsdRate: 17,
    rubCnyRate: 12,
    isLoss: false,
    errors: [],
    raw: {},
  }],
  shippingCostUsd: 83.13,
  shippingCostCny: 581.91,
  totalCostCny: 700,
  netRevenueCny: 403.97,
  profitCny: 100,
  marginPercent: 30,
  usdCnyRate: 7,
  mxnUsdRate: 17,
  rubUsdRate: 78,
  rubCnyRate: 12,
  exchangeRateMode: 'live',
  exchangeRateSource: 'manual',
  exchangeRateFetchedAt: '',
  exchangeRateCached: false,
}

function skuRow(id = 'sku-small', quote: UnknownRecord = {}): DraftSku {
  return {
    sku_id: id, sku: id, selected: true, stock: '10', overrides: {}, attributes_by_target: {}, publications: {},
    pricing: { applied: true, targets: { 'mercadolibre:cbt': {
      shipping_amount: 83.13, shipping_currency: 'USD', shipping_cost_cny: 581.91,
      shipping_quote_mode: 'auto', listing_currency: 'USD',
      suggested_price: { amount: '167.67', currency: 'USD' },
      applied_price: { amount: '167.67', currency: 'USD' },
      calculation_basis: { commission_evidence: { purpose: 'prelisting', source: 'platform_api' }, shipping_evidence: { source: 'ogl_tariff' }, electronic_service: { rate: '.02', rub_per_cny: '12' } },
      profit_cny: 100, margin_percent: 30, is_loss: false, errors: [], ...quote,
    } } },
  }
}


import WorkspaceDialog from '@/components/shared/WorkspaceDialog.vue'
import PricingSkuDetails from '@/components/domain/PricingSkuDetails.vue'
import PricingSkuResults from '@/components/domain/PricingSkuResults.vue'
import * as workspaceApi from '@/api/pricingWorkspace'
import * as warehousesApi from '@/api/yandexWarehouses'

vi.mock('@/api/yandexWarehouses', () => ({ fetchYandexWarehouses: vi.fn() }))
vi.mock('@/api/pricingWorkspace', () => ({
  fetchPricingExchangeRates: vi.fn(), fetchPricingTemplates: vi.fn(), savePricingTemplate: vi.fn(),
  setDefaultPricingTemplate: vi.fn(), deletePricingTemplate: vi.fn(),
}))
enableAutoUnmount(afterEach)
beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(warehousesApi.fetchYandexWarehouses).mockResolvedValue({
    pricing_options: [
      { publish_warehouse_id: 31, publish_warehouse_name: '发布仓 A', route_id: 'economy', route_name: 'CEL Economy' },
      { publish_warehouse_id: 32, publish_warehouse_name: '发布仓 B', route_id: 'express', route_name: 'GUOO Express' },
    ], error: '',
  } as warehousesApi.YandexWarehouseSettings)
  vi.mocked(workspaceApi.fetchPricingExchangeRates).mockResolvedValue({ rates: { usd_cny_rate: 7.12, rub_cny_rate: 12.15, mxn_usd_rate: 18.9 }, fetched_at: '2026-10-18T06:32:00Z', source: 'API', cached: false })
  vi.mocked(workspaceApi.fetchPricingTemplates).mockResolvedValue({ items: [], default_template_id: '' })
})
afterEach(() => vi.useRealTimers())
function productContext(rows: DraftSku[]) {
  return { ...createEmptyDraftProductContext(), skuItems: rows.map(row => ({
    id: row.sku_id, name: row.sku_id, source_sku_id: '', options: {}, cost_cny: '35', supplier_stock: '10',
    image_asset_id: '', barcode: '', active: true, source_snapshot: {},
    package_dimensions: { length_cm: '32', width_cm: '24', height_cm: '8', weight_kg: '0.45' },
  } as ProductSku)) }
}
function mountPricing(rows: DraftSku[], localInput = reactive(structuredClone(input)), savedPricing: UnknownRecord = {}) {
  return mount(PricingPanel, { props: {
    skuItems: rows, input: localInput, result, draftItems: [], draftId: 'draft', draftTitle: '商品', selectionLocked: true,
    productContext: productContext(rows), platformOptions: [], loading: false, savedPricing,
  }, global: { stubs: { teleport: true } } })
}

describe('PricingPanel 对齐 Penpot 核价原型', () => {
  it('只为已选且缺少边长的 SKU 发起估算，单缺重量的规格不计入', async () => {
    setActivePinia(createPinia())
    setOperationAvailability({ desktop_use_cases: [], desktop_reason: '', blocked_use_cases: {} })
    const chat = vi.spyOn(useAiChatStore(), 'estimatePackageDimensions').mockImplementation(() => {})
    const rows = [skuRow('缺长度'), skuRow('缺重量'), { ...skuRow('未选择'), selected: false }]
    const context = productContext(rows)
    context.skuItems[0].package_dimensions.length_cm = ''
    context.skuItems[1].package_dimensions.weight_kg = ''
    context.skuItems[2].package_dimensions.height_cm = ''
    const wrapper = mountPricing(rows)
    await wrapper.setProps({ productContext: context })
    const button = wrapper.findAll('button').find(item => item.text().includes('AI 估算包装尺寸'))!
    expect(button.text()).toContain('1 个 SKU')
    await button.trigger('click')
    expect(chat).toHaveBeenCalledWith('draft', ['缺长度'], ['mercadolibre:cbt'])
    setOperationAvailability(null)
  })
  it.each([
    [{}, true],
    [{ common: { domestic_freight_cny: 0 } }, false],
    [{ targets: { 'mercadolibre:cbt': { payment_fee_percent: 0 } } }, false],
    [{ updated_at: '2026-10-01T00:00:00Z' }, false],
    [{ template_source: { id: 'old', name: '历史模板' } }, false],
  ] as [UnknownRecord, boolean][])('首次核价只为尚无保存参数的草稿填入默认模板：%j', async (savedPricing, expected) => {
    const localInput = reactive(structuredClone(input))
    const parameters = pricingTemplateParameters(localInput)
    parameters.common.domestic_freight_cny = 5
    parameters.common.packaging_cost_cny = 2.5
    vi.mocked(workspaceApi.fetchPricingTemplates).mockResolvedValue({
      default_template_id: 'default', items: [{ id: 'default', name: '默认费用', parameters, updated_at: '' }],
    })
    const row = skuRow()
    row.pricing = { ...row.pricing, targets: {}, applied: false }
    mountPricing([row], localInput, savedPricing)
    await flushPromises()
    expect(localInput.domesticFreightCny).toBe(expected ? 5 : 0)
    expect(localInput.packagingCostCny).toBe(expected ? 2.5 : 0)
    expect(localInput.templateSource?.id).toBe(expected ? 'default' : undefined)
  })

  it('定价方式往返切换保留原报价及已应用状态，不重新计算', async () => {
    const rows = reactive([skuRow()])
    const localInput = reactive(structuredClone(input))
    const wrapper = mountPricing(rows, localInput)
    await flushPromises()
    const before = JSON.stringify(rows)
    const mode = (label: string) => wrapper.get('[aria-label="定价方式"]').findAll('button').find(button => button.text() === label)!

    for (const label of ['成本加价', '手动']) {
      await mode(label).trigger('click')
      expect(wrapper.getComponent(PricingSkuResults).props('stale')).toBe(true)
      expect(wrapper.get('[data-testid="pricing-apply"]').attributes('disabled')).toBeDefined()
      await mode('目标利润率').trigger('click')
      expect(wrapper.get('[data-sku-id="sku-small"]').text()).toContain('$167.67')
      expect(wrapper.get('[data-testid="pricing-apply"]').text()).toBe('已应用至草稿')
      expect(JSON.stringify(rows)).toBe(before)
    }
    expect(wrapper.emitted('calculate')).toBeUndefined()
    expect(wrapper.emitted('restore')).toBeUndefined()
    expect(wrapper.emitted('apply')).toBeUndefined()
  })

  it('切回原定价方式时其他参数仍有改动，继续停用原报价', async () => {
    const localInput = reactive(structuredClone(input))
    const wrapper = mountPricing(reactive([skuRow()]), localInput)
    await flushPromises()
    localInput.targets[0].pricingMode = 'markup'
    localInput.domesticFreightCny = 2
    localInput.targets[0].pricingMode = 'margin'
    await flushPromises()
    expect(wrapper.getComponent(PricingSkuResults).props('stale')).toBe(true)
    expect(wrapper.get('[data-testid="pricing-apply"]').attributes('disabled')).toBeDefined()
    localInput.domesticFreightCny = 0
    await flushPromises()
    expect(wrapper.get('[data-sku-id="sku-small"]').text()).toContain('$167.67')
    expect(wrapper.get('[data-testid="pricing-apply"]').text()).toBe('已应用至草稿')
  })

  it('主动刷新汇率后不能靠切回原定价方式恢复失效报价', async () => {
    const localInput = reactive(structuredClone(input))
    const wrapper = mountPricing(reactive([skuRow()]), localInput)
    await flushPromises()
    await wrapper.get('.pricing-rate-refresh').trigger('click')
    await flushPromises()
    localInput.targets[0].pricingMode = 'markup'
    localInput.targets[0].pricingMode = 'margin'
    await flushPromises()
    expect(wrapper.getComponent(PricingSkuResults).props('stale')).toBe(true)
    expect(wrapper.get('[data-testid="pricing-apply"]').attributes('disabled')).toBeDefined()
    expect(wrapper.emitted('calculate')).toBeUndefined()
  })

  it('重新核价后以新报价的参数为准，切换回来仍可应用新预览', async () => {
    const rows = reactive([skuRow()])
    const localInput = reactive(structuredClone(input))
    const wrapper = mountPricing(rows, localInput)
    await flushPromises()
    await wrapper.setProps({ onCalculate: async () => {
      await wrapper.setProps({ loading: true })
      rows[0].pricing = { ...skuRow().pricing, applied: false }
      await wrapper.setProps({ result: { ...result, previewId: 'new-preview' }, loading: false })
    } })
    localInput.targets[0].pricingMode = 'markup'
    await wrapper.get('[data-testid="pricing-calculate"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-testid="pricing-apply"]').attributes('disabled')).toBeUndefined()
    localInput.targets[0].pricingMode = 'margin'
    await flushPromises()
    expect(wrapper.getComponent(PricingSkuResults).props('stale')).toBe(true)
    localInput.targets[0].pricingMode = 'markup'
    await flushPromises()
    expect(wrapper.get('[data-sku-id="sku-small"]').text()).toContain('$167.67')
    expect(wrapper.get('[data-testid="pricing-apply"]').attributes('disabled')).toBeUndefined()
    expect(rows[0].pricing.applied).toBe(false)
    expect(wrapper.props('result')?.previewId).toBe('new-preview')
    expect(wrapper.emitted('calculate')).toHaveLength(1)
  })

  it('重新核价失败后，切回原模式也不能恢复已失效的报价', async () => {
    const localInput = reactive(structuredClone(input))
    const wrapper = mountPricing(reactive([skuRow()]), localInput)
    await flushPromises()
    await wrapper.setProps({ onCalculate: async () => {
      await wrapper.setProps({ loading: true })
      await wrapper.setProps({ error: '核价失败', loading: false })
    } })
    localInput.targets[0].pricingMode = 'markup'
    await wrapper.get('[data-testid="pricing-calculate"]').trigger('click')
    await flushPromises()
    await wrapper.setProps({ error: '' })
    localInput.targets[0].pricingMode = 'margin'
    await flushPromises()
    expect(wrapper.getComponent(PricingSkuResults).props('stale')).toBe(true)
    expect(wrapper.get('[data-testid="pricing-apply"]').attributes('disabled')).toBeDefined()
  })

  it('返回核价页只恢复有效预览，恢复后允许直接应用售价', async () => {
    const rows = reactive([skuRow()])
    rows[0]!.pricing.applied = false
    const wrapper = mount(PricingPanel, { props: {
      skuItems: rows, input: reactive(structuredClone(input)), result, draftItems: [],
      draftId: 'draft', draftVersion: '保存后的版本', draftTitle: '商品',
      productContext: productContext(rows), platformOptions: [], loading: false,
      onRestore: async () => {
        await wrapper.setProps({ loading: true })
        await wrapper.setProps({ result: { ...result, previewId: 'restored-preview' }, loading: false })
      },
    }, global: { stubs: { teleport: true } } })
    await flushPromises()
    expect(wrapper.emitted('restore')).toHaveLength(1)
    expect(wrapper.emitted('calculate')).toBeUndefined()
    expect(wrapper.text()).toContain('待应用售价')
    wrapper.props('input').targets[0].pricingMode = 'markup'
    await flushPromises()
    expect(wrapper.getComponent(PricingSkuResults).props('stale')).toBe(true)
    wrapper.props('input').targets[0].pricingMode = 'margin'
    await flushPromises()
    expect(wrapper.get('[data-sku-id="sku-small"]').text()).toContain('$167.67')
    expect(wrapper.text()).toContain('待应用售价')
    expect(wrapper.props('result')?.previewId).toBe('restored-preview')
    const apply = wrapper.get('[data-testid="pricing-apply"]')
    expect(apply.attributes('disabled')).toBeUndefined()
    await apply.trigger('click')
    expect(wrapper.emitted('apply')).toHaveLength(1)
    expect(wrapper.emitted('calculate')).toBeUndefined()
  })

  it('切换来源模板即使费用相同也需重新确认核价，不自动计算或应用', async () => {
    const localInput = reactive(structuredClone(input))
    localInput.templateSource = { id: 'first', name: '原模板' }
    const rows = reactive([skuRow()])
    const wrapper = mountPricing(rows, localInput)
    await flushPromises()
    localInput.templateSource = { id: 'second', name: '另一模板' }
    await flushPromises()
    expect(wrapper.text()).toContain('待重新计算')
    expect(wrapper.get('[data-testid="pricing-apply"]').attributes('disabled')).toBeDefined()
    expect(wrapper.emitted('calculate')).toBeUndefined()
    expect(wrapper.emitted('apply')).toBeUndefined()
  })

  it('货损选填，修改共用或 SKU 参数仅停用旧报价，不自动计算', async () => {
    const localInput = reactive(structuredClone(input))
    const rows = reactive([skuRow(), skuRow('sku-large')])
    const wrapper = mountPricing(rows, localInput)
    await flushPromises()
    expect(wrapper.find('[aria-label="每次退货额外费用（人民币）"]').exists()).toBe(false)
    await wrapper.get('[aria-label="预计退货率 %"]').setValue('10')
    await wrapper.get('[aria-label="退货处理方式"]').setValue('resell')
    await wrapper.get('[aria-label="每次退货额外费用（人民币）"]').setValue('40')
    expect(localInput.targets[0]).toMatchObject({ returnRatePercent: 10, returnDisposition: 'resell', returnHandlingCostCny: 40 })
    expect(wrapper.get('[data-testid="pricing-apply"]').attributes('disabled')).toBeDefined()
    expect(wrapper.emitted('calculate')).toBeUndefined()
    await wrapper.get('.pricing-detail-button').trigger('click')
    await wrapper.get('[aria-label="此 SKU 预计退货率 %"]').setValue('0')
    await wrapper.get('[aria-label="此 SKU 退货处理方式"]').setValue('discard')
    await wrapper.get('[aria-label="此 SKU 每次退货额外费用（人民币）"]').setValue('5')
    expect(pricingRecord(rows[0].pricing_overrides?.targets)['mercadolibre:cbt']).toEqual({ return_rate_percent: '0', return_disposition: 'discard', return_handling_cost_cny: '5' })
    expect(rows[1].pricing_overrides).toBeUndefined()
    await wrapper.get('[aria-label="此 SKU 预计退货率 %"]').setValue('')
    expect(pricingRecord(rows[0].pricing_overrides?.targets)['mercadolibre:cbt']).not.toHaveProperty('return_rate_percent')
    expect(wrapper.emitted('calculate')).toBeUndefined()
  })

  it('货损明细显示分摊及本地估算口径，退货率 100% 阻止计算', async () => {
    const localInput = reactive(structuredClone(input))
    const wrapper = mountPricing([skuRow('sku-small', { return_loss_cny: 8.56, return_loss: { return_rate_percent: 10, return_disposition: 'resell', single_loss_cny: 77 } })], localInput)
    await flushPromises()
    await wrapper.get('.pricing-detail-button').trigger('click')
    const detail = wrapper.getComponent(PricingSkuDetails)
    expect(detail.text()).toContain('预计货损分摊')
    expect(detail.text()).toContain('CN¥8.56')
    expect(detail.get('[data-testid="return-loss-detail"]').text()).toContain('本地预估')
    await wrapper.get('[aria-label="预计退货率 %"]').setValue('100')
    expect(wrapper.text()).toContain('预计退货率必须不小于 0 且小于 100%')
  })

  it('枚举仓库物流方式，选择与恢复比价只更新条件，手动点击才计算', async () => {
    const localInput = reactive(structuredClone(input))
    Object.assign(localInput.targets[0], { platform: 'yandex', site: 'global', targetKey: 'yandex:global', sitesToSell: [] })
    const wrapper = mountPricing(reactive([skuRow()]), localInput)
    await flushPromises()
    const select = wrapper.get<HTMLSelectElement>('[aria-label="国际运费报价方式"]')
    expect(select.element.value).toBe('auto')
    expect(select.text()).toContain('发布仓 A · CEL Economy')
    expect(select.text()).toContain('发布仓 B · GUOO Express')
    expect(select.text()).not.toContain('共用手动运费')
    expect(warehousesApi.fetchYandexWarehouses).toHaveBeenCalledTimes(1)
    expect(warehousesApi.fetchYandexWarehouses).toHaveBeenCalledWith()
    await select.setValue(JSON.stringify([32, 'express']))
    expect(localInput.targets[0].shippingSelection).toEqual({ warehouse_id: 32, route_id: 'express' })
    expect(wrapper.get('[data-testid="pricing-apply"]').attributes('disabled')).toBeDefined()
    expect(wrapper.emitted('calculate')).toBeUndefined()
    await wrapper.get('[data-testid="pricing-calculate"]').trigger('click')
    expect(wrapper.emitted('calculate')).toHaveLength(1)
    await select.setValue('auto')
    expect(localInput.targets[0].shippingSelection).toBeNull()
    expect(wrapper.emitted('calculate')).toHaveLength(1)
  })

  it('已失效的指定物流方式保留并提示，不自动退回比价', async () => {
    const localInput = reactive(structuredClone(input))
    Object.assign(localInput.targets[0], { platform: 'yandex', shippingSelection: { warehouse_id: 99, route_id: 'removed' } })
    const wrapper = mountPricing(reactive([skuRow()]), localInput)
    await flushPromises()
    expect(wrapper.get('[aria-label="国际运费报价方式"]').text()).toContain('原物流方式不可用，请重新选择')
    expect(localInput.targets[0].shippingSelection?.warehouse_id).toBe(99)
    expect(wrapper.emitted('calculate')).toBeUndefined()
  })
  it('九个 SKU 的重复超时合并为一组，保留受影响规格及详细错误', async () => {
    const message = 'yandex 官方佣金查询失败（YANDEX_NETWORK_TIMEOUT）：Yandex 接口请求超时'
    const wrapper = mountPricing(Array.from({ length: 9 }, (_, i) => skuRow(`规格 ${i + 1}`, {
      errors: [{ message }, { message }],
    })))
    await wrapper.setProps({ error: '9 个 SKU 核价未完成：Yandex 费用查询超时，请稍后重试。' })
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).not.toContain('YANDEX_NETWORK_TIMEOUT')
    await wrapper.get('.pricing-results-alert button:last-child').trigger('click')
    const drawer = wrapper.findAllComponents(WorkspaceDialog).find(dialog => dialog.props('title') === '处理核价异常')!
    expect(drawer.findAll('article')).toHaveLength(1)
    expect(drawer.get('article h4').text()).toContain('9 项报价')
    expect(drawer.get('article > p').text()).toBe('Yandex 费用查询超时，请稍后重试')
    expect(drawer.get('details').text().split(message)).toHaveLength(2)
    expect(drawer.findAll('details li')).toHaveLength(9)
    expect(wrapper.get('[data-testid="pricing-apply"]').attributes('disabled')).toBeDefined()
    await wrapper.get('.pricing-results-alert button').trigger('click')
    expect(wrapper.emitted('calculate')).toHaveLength(1)
  })
  it('明细抽屉内按下、遮罩松开不关闭，完整遮罩点击关闭', async () => {
    const wrapper = mountPricing([skuRow()])
    await flushPromises()
    await wrapper.get('.pricing-detail-button').trigger('click')
    await flushPromises()
    const detail = wrapper.findAllComponents(WorkspaceDialog).find(dialog => dialog.props('title') === 'SKU 核价明细')!
    const pointer = { isPrimary: true, button: 0, pointerId: 1 }
    await detail.get('input').trigger('pointerdown', pointer)
    await detail.get('dialog').trigger('pointerup', pointer)
    expect(detail.props('open')).toBe(true)
    await detail.get('dialog').trigger('pointerdown', pointer)
    await detail.get('dialog').trigger('pointerup', pointer)
    expect(detail.props('open')).toBe(false)
  })

  it('旧报价及已上架商品佣金回执不能作为上架前有效报价', async () => {
    const wrapper = mountPricing([
      skuRow('历史报价', { calculation_basis: {} }),
      skuRow('已上架查询', { calculation_basis: { commission_evidence: { purpose: 'prelisting', endpoint: '/v5/product/info/prices' } } }),
    ])
    await flushPromises()
    expect(wrapper.get('[data-testid="pricing-apply"]').attributes('disabled')).toBeDefined()
    await wrapper.get('[data-sku-id="历史报价"] .pricing-detail-button').trigger('click')
    expect(wrapper.getComponent(PricingSkuDetails).text()).toContain('历史售价：$167.67')
    expect(wrapper.getComponent(PricingSkuDetails).text()).toContain('历史报价缺少上架前官方佣金依据，请重新核价')
  })

  it('共用区只设报价规则，物流原币与官方依据在明细抽屉展示', async () => {
    const localInput = reactive(structuredClone(input))
    localInput.targets[0] = { ...localInput.targets[0], platform: 'yandex', site: 'global', targetKey: 'yandex:global', shippingCurrency: 'CNY', sitesToSell: [] }
    const row = skuRow()
    row.pricing.targets = { 'yandex:global': { shipping_amount: 15.28, shipping_currency: 'CNY', calculation_basis: {
      commission_evidence: { purpose: 'prelisting', source: 'platform_api' },
      shipping_evidence: { fulfillment_plan: { fingerprint: 'plan-1', route_name: 'CEL Economy', publish_warehouse_id: 31, publish_warehouse_name: 'CEL 发布仓', receiving_warehouse_name: '义乌合作仓', address_name: 'CEL 收货点' }, route: 'CEL Economy', original_amount: '183.33', original_currency: 'RUB', billable_g: '367', tariff_version: 'yandex-test-version' },
    } } }
    const wrapper = mountPricing([row], localInput)
    await flushPromises()
    expect(wrapper.get('option[value="auto"]').attributes('disabled')).toBeUndefined()
    expect(wrapper.get('[data-testid="pricing-market-defaults"]').text()).not.toContain('183.33')
    await wrapper.get('.pricing-detail-button').trigger('click')
    const detail = wrapper.getComponent(PricingSkuDetails)
    expect(detail.text()).toContain('183.33 RUB')
    expect(detail.text()).toContain('yandex-test-version')
    expect(detail.text()).toContain('CEL 发布仓')
    expect(detail.text()).not.toContain('合作收货仓')
    expect(wrapper.get('.pricing-results-table').text()).toContain('发布仓：CEL 发布仓')
    expect(wrapper.findAllComponents(WorkspaceDialog).find(dialog => dialog.props('title') === 'SKU 核价明细')?.props('variant')).toBe('drawer')
    expect(wrapper.find('select[aria-label="国际运费报价币种"]').exists()).toBe(false)
  })

  it('范围只选择 SKU 和市场，运输属性由商品持有', async () => {
    const wrapper = mountPricing(reactive([skuRow()]))
    await flushPromises()
    await wrapper.findAll('button').find(button => button.text() === '修改核价范围')!.trigger('click')
    const scope = wrapper.findAllComponents(WorkspaceDialog).find(dialog => dialog.props('title') === '核价范围')!
    expect(scope.text()).not.toContain('含电池')
    expect(scope.text()).not.toContain('含液体')
  })

  it('明细区分 Mercado 买家售价与期望到账额', async () => {
    const wrapper = mountPricing([skuRow('sku-small', { applied_net_proceeds: { amount: '57.71', currency: 'USD' } })])
    await flushPromises()
    await wrapper.get('.pricing-detail-button').trigger('click')
    const detail = wrapper.getComponent(PricingSkuDetails)
    expect(detail.text()).toContain('建议售价 · USD')
    expect(detail.text()).toContain('167.67')
    expect(detail.text()).toContain('Mercado 期望到账额 $57.71')
    expect(detail.text()).toContain('不是买家看到的售价')
  })

  it('各 SKU 的运费和利润保持独立，亏损报价阻止应用', async () => {
    const wrapper = mountPricing([skuRow(), skuRow('sku-large', { shipping_amount: 99.5, shipping_cost_cny: 696.5, profit_cny: -12, is_loss: true })])
    await flushPromises()
    expect(wrapper.get('[data-sku-id="sku-small"]').text()).toContain('CN¥581.91')
    expect(wrapper.get('[data-sku-id="sku-small"]').text()).not.toContain('CN¥696.50')
    expect(wrapper.get('[data-sku-id="sku-large"]').text()).toContain('CN¥696.50')
    expect(wrapper.get('[data-sku-id="sku-large"]').text()).toContain('亏损')
    expect(wrapper.get('[data-testid="pricing-market-defaults"]').text()).not.toContain('$83.13')
    expect(wrapper.get('[data-testid="pricing-apply"]').attributes('disabled')).toBeDefined()
  })

  it('单独调整只写入当前 SKU 和市场，清空后恢复共用规则', async () => {
    const rows = reactive([skuRow(), skuRow('sku-large')])
    const wrapper = mountPricing(rows)
    await flushPromises()
    await wrapper.get('[data-sku-id="sku-large"] .pricing-detail-button').trigger('click')
    const priceInput = () => wrapper.getComponent(PricingSkuDetails).findAll('label').find(label => label.text().includes('此 SKU 手动售价'))!.get('input')
    const shippingInput = () => wrapper.get('input[aria-label="此 SKU 手动运费（人民币）"]')
    await priceInput().setValue('200')
    await shippingInput().setValue('88')
    expect(rows[1].pricing_overrides?.targets).toEqual({ 'mercadolibre:cbt': { manual_price: { amount: '200', currency: 'USD' }, shipping_amount: 88 } })
    expect(rows[1].pricing.applied).toBe(false)
    expect(rows[0].pricing_overrides).toBeUndefined()
    expect(wrapper.props('input').targets[0].manualPrice).toBeNull()
    await priceInput().setValue('')
    await shippingInput().setValue('')
    expect(rows[1].pricing_overrides?.targets).toEqual({ 'mercadolibre:cbt': {} })
  })

  it('198 个 SKU 连续展示无分页，搜索作用于完整列表', async () => {
    const rows = Array.from({ length: 198 }, (_, index) => ({ ...skuRow(`规格-${index}`), pricing: {} }))
    const wrapper = mountPricing(rows)
    await flushPromises()
    expect(wrapper.findAll('tr[data-sku-id]')).toHaveLength(198)
    expect(wrapper.get('[data-sku-id="规格-0"]').text()).toContain('待核价')
    expect(wrapper.getComponent(PricingSkuResults).text()).not.toContain('下一页')
    await wrapper.get('input[aria-label="搜索规格或 SKU"]').setValue('规格-197')
    expect(wrapper.findAll('tr[data-sku-id]')).toHaveLength(1)
    expect(wrapper.find('[data-sku-id="规格-197"]').exists()).toBe(true)
  })

  it('按 SKU 合并采购与包装，市场和异常筛选作用于所有报价', async () => {
    const row = skuRow()
    const localInput = reactive(structuredClone(input))
    localInput.targets.push({ ...localInput.targets[0], targetKey: 'yandex:global', platform: 'yandex', site: 'global' })
    const wrapper = mountPricing([row], localInput)
    await flushPromises()
    expect(wrapper.get('th[scope="rowgroup"]').attributes('rowspan')).toBe('2')
    expect(wrapper.get('[data-target-key="yandex:global"]').text()).toContain('待核价')
    await wrapper.get('[aria-label="筛选核价市场"]').findAll('button').find(button => button.text() === 'Yandex 1')!.trigger('click')
    expect(wrapper.findAll('tr[data-sku-id]')).toHaveLength(1)
    expect(wrapper.find('[data-target-key="mercadolibre:cbt"]').exists()).toBe(false)
    expect(wrapper.get('[data-testid="pricing-apply"]').attributes('disabled')).toBeDefined()
  })

  it('进入、重新进入和修改参数不请求核价，只有手动计算才触发', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    const rows = reactive([skuRow()])
    const localInput = reactive(structuredClone(input))
    const wrapper = mountPricing(rows, localInput)
    await flushPromises()
    await vi.advanceTimersByTimeAsync(2000)
    expect(wrapper.emitted('calculate')).toBeUndefined()
    await wrapper.get('input[aria-label="国内物流（CNY）"]').setValue('2')
    await vi.advanceTimersByTimeAsync(2000)
    expect(wrapper.emitted('calculate')).toBeUndefined()
    expect(wrapper.get('[data-testid="pricing-apply"]').attributes('disabled')).toBeDefined()
    const firstClick = wrapper.get('[data-testid="pricing-calculate"]').trigger('click')
    const secondClick = wrapper.get('[data-testid="pricing-calculate"]').trigger('click')
    await Promise.all([firstClick, secondClick])
    expect(wrapper.emitted('calculate')).toHaveLength(1)
    wrapper.unmount()
    const reopened = mountPricing(rows, localInput)
    await flushPromises()
    await vi.advanceTimersByTimeAsync(2000)
    expect(reopened.emitted('calculate')).toBeUndefined()
  })

  it('九个 SKU 的自动运费按核价快照显示人民币，保留已应用结果且不新增请求', async () => {
    const rows = reactive(Array.from({ length: 9 }, (_, index) => skuRow(`sku-${index}`)))
    const localInput = reactive(structuredClone(input))
    const before = JSON.stringify(rows)
    const wrapper = mountPricing(rows, localInput)
    await flushPromises()
    expect(wrapper.find('select[aria-label="国际运费报价币种"]').exists()).toBe(false)
    expect(wrapper.findAll('[data-sku-id]')).toHaveLength(9)
    for (const row of wrapper.findAll('[data-sku-id]')) {
      // 参考汇率为 7.12，核价快照使用 7；展示不能跟随参考值变动。
      expect(row.text()).toContain('CN¥581.91')
      expect(row.text()).toContain('$167.67')
      expect(row.text()).toContain('CNY · 自动')
    }
    expect(wrapper.get('[data-testid="pricing-apply"]').text()).toBe('已应用至草稿')
    expect(JSON.stringify(rows)).toBe(before)
    expect(localInput.targets[0].shippingCurrency).toBe('USD')
    expect(wrapper.emitted('calculate')).toBeUndefined()
    expect(workspaceApi.fetchPricingExchangeRates).toHaveBeenCalledTimes(1)
  })

  it('旧美元手动运费按人民币展示，编辑时同步换算未选 SKU 覆盖且不自动计算', async () => {
    const rows = reactive([skuRow('manual', { shipping_quote_mode: 'manual' }), skuRow('other')])
    rows[1].selected = false
    rows[1].pricing_overrides = { targets: { 'mercadolibre:cbt': { shipping_amount: 10, manual_price: { amount: '150', currency: 'USD' } } } }
    const localInput = reactive(structuredClone(input))
    localInput.targets[0].shippingQuoteMode = 'manual'
    localInput.targets[0].shippingAmount = 83.13
    const wrapper = mountPricing(rows, localInput)
    await flushPromises()
    expect(wrapper.find('select[aria-label="国际运费报价币种"]').exists()).toBe(false)
    expect(wrapper.get('[data-sku-id="manual"]').text()).toContain('CN¥581.91')
    const field = wrapper.get<HTMLInputElement>('input[aria-label="共用每件国际运费（人民币）"]')
    expect(field.element.value).toBe('591.89')
    expect(wrapper.get('[data-testid="pricing-apply"]').text()).toBe('已应用至草稿')
    expect(localInput.targets[0].shippingCurrency).toBe('USD')
    await field.setValue('100')
    expect(localInput.targets[0].shippingCurrency).toBe('CNY')
    expect(localInput.targets[0].shippingAmount).toBe(100)
    expect(pricingRecord(pricingRecord(rows[1].pricing_overrides?.targets)['mercadolibre:cbt'])).toEqual({
      shipping_amount: 71.2, manual_price: { amount: '150', currency: 'USD' },
    })
    expect(rows[0].pricing.applied).toBe(false)
    expect(wrapper.get('[data-testid="pricing-apply"]').attributes('disabled')).toBeDefined()
    expect(wrapper.get('[data-sku-id="manual"]').text()).toContain('等待重新核价')
    expect(wrapper.emitted('calculate')).toBeUndefined()
    await wrapper.get('input[aria-label="USD/CNY 汇率"]').setValue('8')
    expect(field.element.value).toBe('100')
    expect(pricingRecord(pricingRecord(rows[1].pricing_overrides?.targets)['mercadolibre:cbt']).shipping_amount).toBe(71.2)
  })

  it('SKU 运费人民币编辑使用覆盖汇率迁移，清空恢复共用值且不影响其他市场', async () => {
    const rows = reactive([skuRow(), skuRow('other')])
    rows[0].pricing_overrides = { targets: { 'mercadolibre:cbt': { shipping_amount: 10 }, 'ozon:global': { shipping_amount: 9 } } }
    const localInput = reactive(structuredClone(input))
    localInput.targets[0].shippingAmount = 20
    localInput.exchangeRateOverrides = { usd_cny_rate: 7 }
    const wrapper = mountPricing(rows, localInput)
    await flushPromises()
    await wrapper.get('[data-sku-id="sku-small"] .pricing-detail-button').trigger('click')
    const field = wrapper.get<HTMLInputElement>('input[aria-label="此 SKU 手动运费（人民币）"]')
    expect(field.element.value).toBe('70')
    await field.setValue('88')
    expect(localInput.targets[0].shippingCurrency).toBe('CNY')
    expect(localInput.targets[0].shippingAmount).toBe(140)
    expect(pricingRecord(pricingRecord(rows[0].pricing_overrides?.targets)['mercadolibre:cbt']).shipping_amount).toBe(88)
    expect(pricingRecord(pricingRecord(rows[0].pricing_overrides?.targets)['ozon:global']).shipping_amount).toBe(9)
    expect(rows[1].pricing_overrides).toBeUndefined()
    await wrapper.get('input[aria-label="此 SKU 手动运费（人民币）"]').setValue('')
    expect(pricingRecord(pricingRecord(rows[0].pricing_overrides?.targets)['mercadolibre:cbt']).shipping_amount).toBeUndefined()
    expect(wrapper.emitted('calculate')).toBeUndefined()
  })

  it('外币旧运费缺少汇率时不能误改标；人民币运费无需汇率也能编辑', async () => {
    vi.mocked(workspaceApi.fetchPricingExchangeRates).mockRejectedValue(new Error('汇率不可用'))
    const localInput = reactive(structuredClone(input))
    Object.assign(localInput.targets[0], { shippingQuoteMode: 'manual', shippingAmount: 10 })
    const wrapper = mountPricing(reactive([skuRow()]), localInput)
    await flushPromises()
    const field = wrapper.get<HTMLInputElement>('input[aria-label="共用每件国际运费（人民币）"]')
    expect(field.attributes('disabled')).toBeDefined()
    expect(field.element.value).toBe('')
    expect(localInput.targets[0].shippingAmount).toBe(10)
    localInput.targets[0].shippingCurrency = 'CNY'
    await flushPromises()
    expect(field.attributes('disabled')).toBeUndefined()
    expect(field.element.value).toBe('10')
    await field.setValue('25')
    expect(localInput.targets[0].shippingAmount).toBe(25)
    expect(wrapper.emitted('calculate')).toBeUndefined()
  })

  it('父级在请求前拒绝校验时，不锁死手动计算入口', async () => {
    const wrapper = mountPricing(reactive([skuRow()]))
    await flushPromises()
    await wrapper.setProps({ error: '请先确认当前店铺的销售国家' })
    await wrapper.get('[data-testid="pricing-calculate"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-testid="pricing-calculate"]').attributes('disabled')).toBeUndefined()
    await wrapper.get('[data-testid="pricing-calculate"]').trigger('click')
    expect(wrapper.emitted('calculate')).toHaveLength(2)
  })

  it('刷新汇率和刷新数据只更新参考值，不触发物流和佣金试算', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    const wrapper = mountPricing(reactive([skuRow()]))
    await flushPromises()
    await wrapper.get('.pricing-rate-refresh').trigger('click')
    await flushPromises()
    await wrapper.findAll('button').find(button => button.text() === '刷新数据')!.trigger('click')
    await flushPromises()
    await vi.advanceTimersByTimeAsync(2000)
    expect(workspaceApi.fetchPricingExchangeRates).toHaveBeenCalledTimes(3)
    expect(wrapper.emitted('calculate')).toBeUndefined()
    expect(wrapper.get('[data-testid="pricing-apply"]').attributes('disabled')).toBeDefined()
  })

  it('计算期间的新修改停用旧结果，完成后仍需手动重新计算', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    const rows = reactive([skuRow()])
    const localInput = reactive(structuredClone(input))
    const wrapper = mountPricing(rows, localInput)
    await flushPromises()
    await Promise.all([
      wrapper.get('[data-testid="pricing-calculate"]').trigger('click'),
      wrapper.setProps({ loading: true, result: null }),
    ])
    expect(wrapper.emitted('calculate')).toHaveLength(1)
    await wrapper.get('input[aria-label="国内物流（CNY）"]').setValue('2')
    await wrapper.get('input[aria-label="国内物流（CNY）"]').setValue('3')
    await wrapper.setProps({ loading: false, result })
    expect(wrapper.get('[data-testid="pricing-apply"]').attributes('disabled')).toBeDefined()
    await vi.advanceTimersByTimeAsync(550)
    expect(wrapper.emitted('calculate')).toHaveLength(1)
    await Promise.all([
      wrapper.get('[data-testid="pricing-calculate"]').trigger('click'),
      wrapper.setProps({ loading: true, result: null }),
    ])
    expect(wrapper.emitted('calculate')).toHaveLength(2)
    await wrapper.setProps({ loading: false, result: { ...structuredClone(result), previewId: 'preview-1' } })
    await flushPromises()
    expect(wrapper.get('[data-testid="pricing-apply"]').attributes('disabled')).toBeUndefined()
  })

  it('请求失败时停用旧报价，且不会无条件自动重试', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    const wrapper = mountPricing(reactive([skuRow()]))
    await flushPromises()
    await Promise.all([
      wrapper.get('[data-testid="pricing-calculate"]').trigger('click'),
      wrapper.setProps({ loading: true, result: null }),
    ])
    await wrapper.setProps({ loading: false, error: '店铺授权已失效' })
    await flushPromises()
    expect(wrapper.get('[data-sku-id="sku-small"]').text()).not.toContain('CN¥581.91')
    expect(wrapper.get('[data-testid="pricing-apply"]').attributes('disabled')).toBeDefined()
    await vi.advanceTimersByTimeAsync(2000)
    expect(wrapper.emitted('calculate')).toHaveLength(1)
  })

  it('缺失资料引导到对应商品 SKU，草稿不保存来源覆盖', async () => {
    const rows = reactive([skuRow(), skuRow('sku-large')])
    const wrapper = mountPricing(rows)
    const context = productContext(rows)
    context.skuItems[1].package_dimensions.weight_kg = ''
    await wrapper.setProps({ productContext: context })
    await flushPromises()
    await wrapper.get('[data-sku-id="sku-large"] button').trigger('click')
    expect(wrapper.emitted('editProduct')).toEqual([['sku-large']])
    expect(rows[1].overrides.package_dimensions).toBeUndefined()
    expect(wrapper.emitted('calculate')).toBeUndefined()
  })
})

it('运费版本更新后旧报价提示重算，新报价仍可用', () => {
  const row = skuRow()
  const quote = pricingRecord(row.pricing.targets)['mercadolibre:cbt'] as UnknownRecord
  pricingRecord(pricingRecord(quote.calculation_basis).shipping_evidence).tariff_version = 'ozon-old'
  row.pricing.targets = { 'ozon:global': quote }
  const target = { ...input.targets[0], platform: 'ozon' as const, site: 'global', targetKey: 'ozon:global' }
  const entries = buildSkuPricingEntries([row], productContext([row]).skuItems, [target], { ozon: 'ozon-new' })
  expect(entries[0].errors).toContain('国际物流运费表已更新，请重新核价')
  expect(entries[0].status).toBe('error')
  const current = buildSkuPricingEntries([row], productContext([row]).skuItems, [target], { ozon: 'ozon-old' })
  expect(current[0].errors).toEqual([])
})
