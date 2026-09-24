// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { reactive } from 'vue'
import { describe, expect, it } from 'vitest'
import PricingPanel from '@/components/domain/PricingPanel.vue'
import { createEmptyDraftProductContext } from '@/constants/initialState'
import type { DraftSku, PricingInput, PricingResult, UnknownRecord } from '@/types/workflow'

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
  exchangeRateMode: 'manual',
  targets: [{
    targetKey: 'mercadolibre:cbt',
    platform: 'mercadolibre',
    site: 'CBT',
    sitesToSell: [{ siteId: 'MLM', logisticType: 'remote' }],
    listingCurrency: 'USD',
    commissionPercent: 16,
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
  exchangeRateMode: 'manual',
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
      profit_cny: 100, margin_percent: 30, is_loss: false, errors: [], ...quote,
    } } },
  }
}

function mountPricing(rows: DraftSku[], localInput = reactive(structuredClone(input))) {
  return mount(PricingPanel, { props: {
    skuItems: rows, input: localInput, result, draftItems: [], draftId: 'draft', draftTitle: '商品',
    productContext: createEmptyDraftProductContext(), platformOptions: [], loading: false,
  } })
}

describe('PricingPanel', () => {
  it('Yandex 共用区只设报价规则，原币与依据在对应 SKU 明细展示', async () => {
    const localInput = structuredClone(input)
    localInput.targets[0] = { ...localInput.targets[0], platform: 'yandex', site: 'global', targetKey: 'yandex:global', shippingCurrency: 'CNY', shippingAmount: 15.28, sitesToSell: [] }
    const localResult = structuredClone(result)
    localResult.results[0] = { ...localResult.results[0], ...localInput.targets[0],
      calculationBasis: { shipping_evidence: {
        route: 'CEL Economy', original_amount: '183.33', original_currency: 'RUB',
        billable_g: '367', tariff_version: 'yandex-test-version', exchange_rate: '0.083333', quoted_at: '2026-09-21',
      } },
    }
    const row = skuRow()
    row.pricing.targets = { 'yandex:global': { shipping_amount: 15.28, shipping_currency: 'CNY', calculation_basis: localResult.results[0].calculationBasis } }
    const wrapper = mount(PricingPanel, { props: {
      skuItems: [row], input: localInput, result: localResult, draftItems: [], draftId: 'draft', draftTitle: '商品',
      productContext: createEmptyDraftProductContext(), platformOptions: [], loading: false,
    } })
    expect(wrapper.get('option[value="auto"]').attributes('disabled')).toBeUndefined()
    expect(wrapper.text()).toContain('自动获取最低运费')
    expect(wrapper.get('[data-testid="pricing-market-defaults"]').text()).not.toContain('183.33')
    expect(wrapper.get('[data-testid="pricing-market-defaults"]').find('input[placeholder="核价时自动填写"]').exists()).toBe(false)
    await wrapper.findAll('button').find(button => button.text() === '查看明细')!.trigger('click')
    expect(wrapper.text()).toContain('183.33 RUB')
    expect(wrapper.text()).toContain('yandex-test-version')
    const currencySelect = wrapper.findAll('select').find(select => select.find('option[value="CNY"]').exists())!
    expect(currencySelect.attributes('disabled')).toBeUndefined()
    expect((currencySelect.element as HTMLSelectElement).value).toBe('CNY')
  })

  it('修改电池或液体信息复用既有核价失效机制', async () => {
    const localInput = reactive(structuredClone(input))
    const rows = reactive([{ pricing: { applied: true } }]) as unknown as DraftSku[]
    const wrapper = mount(PricingPanel, { props: {
      skuItems: rows, input: localInput, result: null, draftItems: [], draftId: 'draft', draftTitle: '商品',
      productContext: createEmptyDraftProductContext(), platformOptions: [], loading: false,
    } })
    const battery = wrapper.findAll('label').find(label => label.text().includes('所选 SKU 含电池'))!
    await battery.get('input').setValue(true)
    expect(localInput.battery).toBe(true)
    expect(rows[0].pricing.applied).toBe(false)
  })

  it('在对应 SKU 明细区分买家售价与 Mercado 期望到账额', async () => {
    const wrapper = mount(PricingPanel, {
      props: {
        skuItems: [skuRow('sku-small', { applied_net_proceeds: { amount: '57.71', currency: 'USD' } })],
        input,
        result,
        draftItems: [],
        draftId: 'draft-cbt',
        draftTitle: '狗屋',
        productContext: createEmptyDraftProductContext(),
        platformOptions: [{
          key: 'mercadolibre',
          label: '美客多',
          sites: [{ key: 'CBT', code: 'CBT', label: 'Global Selling', language: 'en-US' }],
        }],
        loading: false,
      },
    })

    await wrapper.findAll('button').find(button => button.text() === '查看明细')!.trigger('click')
    expect(wrapper.text()).toContain('本次买家售价')
    expect(wrapper.text()).toContain('Mercado 期望到账额')
    expect(wrapper.text()).toContain('不是买家看到的售价')
  })

  it('各 SKU 的运费和利润保持独立，任一规格亏损都会阻止应用', () => {
    const wrapper = mountPricing([
      skuRow(),
      skuRow('sku-large', { shipping_amount: 99.5, profit_cny: -12, is_loss: true }),
    ])
    const small = wrapper.get('[data-sku-id="sku-small"]')
    const large = wrapper.get('[data-sku-id="sku-large"]')
    expect(small.text()).toContain('$83.13')
    expect(small.text()).not.toContain('$99.50')
    expect(large.text()).toContain('$99.50')
    expect(large.text()).toContain('亏损')
    expect(wrapper.get('[data-testid="pricing-market-defaults"]').text()).not.toContain('$83.13')
    expect(wrapper.findAll('button').find(button => button.text() === '应用售价')!.attributes('disabled')).toBeDefined()
  })

  it('单独调整只写入当前 SKU 和市场，清空后恢复共用规则', async () => {
    const rows = reactive([skuRow(), skuRow('sku-large')])
    const wrapper = mountPricing(rows)
    await wrapper.get('[data-sku-id="sku-large"] button').trigger('click')
    const priceInput = wrapper.findAll('label').find(label => label.text().includes('此 SKU 手动售价'))!.get('input')
    const shippingInput = wrapper.findAll('label').find(label => label.text().includes('此 SKU 手动运费'))!.get('input')
    await priceInput.setValue('200')
    await shippingInput.setValue('88')
    expect(rows[1].pricing_overrides?.targets).toEqual({ 'mercadolibre:cbt': { manual_price: { amount: '200', currency: 'USD' }, shipping_amount: '88' } })
    expect(rows[1].pricing.applied).toBe(false)
    expect(rows[0].pricing.applied).toBe(true)
    expect(rows[0].pricing_overrides).toBeUndefined()
    expect(wrapper.props('input').targets[0].manualPrice).toBeNull()
    await priceInput.setValue('')
    await shippingInput.setValue('')
    expect(rows[1].pricing_overrides?.targets).toEqual({ 'mercadolibre:cbt': {} })
  })

  it('未报价明确显示待核价，多规格支持分页和名称搜索', async () => {
    const rows = Array.from({ length: 25 }, (_, index) => ({ ...skuRow(`规格-${index}`), pricing: {} }))
    const wrapper = mountPricing(rows)
    expect(wrapper.findAll('tr[data-sku-id]')).toHaveLength(20)
    expect(wrapper.get('[data-sku-id="规格-0"]').text()).toContain('待核价')
    expect(wrapper.get('[data-sku-id="规格-0"]').text()).not.toContain('$0.00')
    await wrapper.findAll('button').find(button => button.text() === '下一页')!.trigger('click')
    expect(wrapper.findAll('tr[data-sku-id]')).toHaveLength(5)
    await wrapper.get('input[type="search"]').setValue('规格-24')
    expect(wrapper.findAll('tr[data-sku-id]')).toHaveLength(1)
    expect(wrapper.find('[data-sku-id="规格-24"]').exists()).toBe(true)
    await wrapper.findAll('button').find(button => button.text() === '收起 SKU')!.trigger('click')
    expect(wrapper.get('table').isVisible()).toBe(false)
    expect(wrapper.get('[data-testid="pricing-market-defaults"]').isVisible()).toBe(true)
  })

  it('已移除市场的旧报价不展示，当前目标缺少结果时不能应用', () => {
    const row = skuRow()
    ;(row.pricing.targets as UnknownRecord)['ozon:global'] = { shipping_amount: 12345, shipping_currency: 'CNY' }
    const localInput = reactive(structuredClone(input))
    localInput.targets.push({ ...localInput.targets[0], targetKey: 'yandex:global', platform: 'yandex', site: 'global' })
    const wrapper = mountPricing([row], localInput)
    expect(wrapper.findAll('tr[data-sku-id]')).toHaveLength(2)
    expect(wrapper.find('[data-target-key="ozon:global"]').exists()).toBe(false)
    expect(wrapper.get('[data-target-key="yandex:global"]').text()).toContain('待核价')
    expect(wrapper.findAll('button').find(button => button.text() === '应用售价')!.attributes('disabled')).toBeDefined()
  })
})
