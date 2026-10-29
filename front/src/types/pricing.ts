import type { PricingInput, PricingTargetInput } from './workflow'

export type ExchangeRateKey = 'usd_cny_rate' | 'rub_cny_rate' | 'mxn_usd_rate'
export type ExchangeRateOverrides = Partial<Record<ExchangeRateKey, number>>

export interface PricingExchangeRates {
  rates: Record<ExchangeRateKey, number>
  source: string
  fetched_at: string
  cached: boolean
  stale?: boolean
  error?: string
}

export interface PricingTemplateParameters {
  common: {
    domestic_freight_cny: number
    packaging_cost_cny: number
    other_cost_cny: number
    exchange_rate_overrides: ExchangeRateOverrides
  }
  targets: Record<string, {
    return_rate_percent?: number
    return_disposition?: 'discard' | 'resell'
    return_handling_cost_cny?: number
    payment_fee_percent: number
    other_fee_percent: number
    pricing_mode: PricingTargetInput['pricingMode']
    target_margin_percent: number
    markup_percent: number
    shipping_quote_mode: PricingTargetInput['shippingQuoteMode']
    shipping_currency: PricingTargetInput['shippingCurrency']
    shipping_amount: number
    shipping_selection?: PricingTargetInput['shippingSelection']
    manual_price?: PricingTargetInput['manualPrice']
  }>
}

export interface PricingTemplate {
  id: string
  name: string
  parameters: PricingTemplateParameters
  updated_at: string
}

export interface PricingTemplateSettings {
  items: PricingTemplate[]
  default_template_id: string
  saved_id?: string
}

export function selectedPricingTargets(input: PricingInput): PricingTargetInput[] {
  return input.selectedTargetKeys ? input.targets.filter(target => input.selectedTargetKeys!.includes(target.targetKey)) : input.targets
}

export function pricingTemplateParameters(input: PricingInput, includeRates = false): PricingTemplateParameters {
  return {
    common: {
      domestic_freight_cny: input.domesticFreightCny,
      packaging_cost_cny: input.packagingCostCny,
      other_cost_cny: input.otherCostCny,
      exchange_rate_overrides: includeRates ? { ...input.exchangeRateOverrides } : {},
    },
    targets: Object.fromEntries(selectedPricingTargets(input).map(target => [target.targetKey.toLowerCase(), {
      return_rate_percent: target.returnRatePercent === '' ? 0 : target.returnRatePercent ?? 0,
      return_disposition: target.returnDisposition || 'discard',
      return_handling_cost_cny: target.returnHandlingCostCny === '' ? 0 : target.returnHandlingCostCny ?? 0,
      payment_fee_percent: target.paymentFeePercent,
      other_fee_percent: target.otherFeePercent,
      pricing_mode: target.pricingMode,
      target_margin_percent: target.targetMarginPercent,
      markup_percent: target.markupPercent,
      shipping_quote_mode: target.shippingQuoteMode,
      shipping_currency: target.shippingCurrency,
      shipping_amount: target.shippingQuoteMode === 'manual' ? target.shippingAmount : 0,
      shipping_selection: target.shippingSelection || null,
      manual_price: target.pricingMode === 'manual' ? target.manualPrice : null,
    }])),
  }
}

export function applyPricingTemplate(input: PricingInput, template: PricingTemplate) {
  const { common, targets } = template.parameters
  input.templateSource = { id: template.id, name: template.name }
  input.domesticFreightCny = common.domestic_freight_cny
  input.packagingCostCny = common.packaging_cost_cny
  input.otherCostCny = common.other_cost_cny
  input.exchangeRateMode = 'live'
  input.exchangeRateOverrides = { ...common.exchange_rate_overrides }
  for (const target of input.targets) {
    const saved = targets[target.targetKey.toLowerCase()]
    if (!saved) continue
    Object.assign(target, {
      returnRatePercent: saved.return_rate_percent ?? 0,
      returnDisposition: saved.return_disposition || 'discard',
      returnHandlingCostCny: saved.return_handling_cost_cny ?? 0,
      paymentFeePercent: saved.payment_fee_percent,
      otherFeePercent: saved.other_fee_percent,
      pricingMode: saved.pricing_mode,
      targetMarginPercent: saved.target_margin_percent,
      markupPercent: saved.markup_percent,
      shippingQuoteMode: target.platform === 'ozon' ? 'auto' : saved.shipping_quote_mode,
      shippingCurrency: saved.shipping_currency,
      shippingAmount: target.platform === 'ozon' ? 0 : saved.shipping_amount,
      shippingSelection: saved.shipping_selection ? { ...saved.shipping_selection } : null,
      manualPrice: saved.manual_price ? { ...saved.manual_price } : null,
    })
  }
}
