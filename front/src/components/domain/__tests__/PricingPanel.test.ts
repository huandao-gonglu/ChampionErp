// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import PricingPanel from '@/components/domain/PricingPanel.vue'
import { createEmptyDraftProductContext } from '@/constants/initialState'
import type { PricingInput, PricingResult } from '@/types/workflow'

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
    shippingSource: 'system_estimate',
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

describe('PricingPanel', () => {
  it('明确区分买家售价与 Mercado 期望到账额', () => {
    const wrapper = mount(PricingPanel, {
      props: {
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

    expect(wrapper.text()).toContain('本次买家售价')
    expect(wrapper.text()).toContain('Mercado 期望到账额')
    expect(wrapper.text()).toContain('不是买家看到的售价')
  })
})
