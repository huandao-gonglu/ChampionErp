import type { DraftSku, PricingTargetInput, ProductSku, UnknownRecord } from '@/types/workflow'

export function pricingRecord(value: unknown): UnknownRecord {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as UnknownRecord : {}
}

export function shippingCostCny(quote: UnknownRecord): unknown {
  // 展示本次核价快照，不使用后来刷新或修改的参考汇率。
  if (quote.shipping_cost_cny != null) return quote.shipping_cost_cny
  if (quote.shipping_currency === 'CNY') return quote.shipping_amount
  const rate = Number(quote.usd_cny_rate ?? pricingRecord(quote.calculation_basis).usd_cny_rate)
  if (quote.shipping_currency === 'USD' && Number.isFinite(rate) && rate > 0
    && quote.shipping_amount != null && quote.shipping_amount !== '' && Number.isFinite(Number(quote.shipping_amount))) {
    return Number(quote.shipping_amount) * rate
  }
  return undefined
}

export interface SkuPricingEntry {
  key: string
  row: DraftSku
  target: PricingTargetInput
  name: string
  cost: unknown
  dimensions: UnknownRecord
  quote: UnknownRecord | null
  errors: string[]
  status: 'pending' | 'error' | 'loss' | 'applied' | 'preview'
}

export function buildSkuPricingEntries(rows: DraftSku[], skus: ProductSku[], targets: PricingTargetInput[]): SkuPricingEntry[] {
  const skuById = new Map(skus.map(sku => [sku.id, sku]))
  return rows.filter(row => row.selected).flatMap(row => {
    const sku = skuById.get(row.sku_id)
    const quotes = Object.fromEntries(Object.entries(pricingRecord(row.pricing.targets)).map(([key, value]) => [key.toLowerCase(), value]))
    return targets.map(target => {
      const saved = pricingRecord(quotes[target.targetKey.toLowerCase()])
      const quote = Object.keys(saved).length ? saved : null
      const errors = [...new Set((Array.isArray(quote?.errors) ? quote.errors : []).map(error => (
        typeof error === 'string' ? error : String(pricingRecord(error).message || pricingRecord(error).field || '')
      )).filter(Boolean))]
      const commission = pricingRecord(pricingRecord(quote?.calculation_basis).commission_evidence)
      if (quote && !errors.length && (commission.purpose !== 'prelisting'
        || ['/v5/product/info/prices', '/v3/product/info/list'].includes(String(commission.endpoint)))) {
        errors.push('历史报价缺少上架前官方佣金依据，请重新核价')
      }
      if (quote && !errors.length && target.platform === 'ozon'
        && (pricingRecord(pricingRecord(quote.calculation_basis).shipping_evidence).source !== 'ogl_tariff'
          || !pricingRecord(quote.calculation_basis).electronic_service)) {
        errors.push('此报价尚未使用 OGL，请重新核价')
      }
      if (quote && !errors.length && target.platform === 'yandex'
        && !pricingRecord(pricingRecord(pricingRecord(quote.calculation_basis).shipping_evidence).fulfillment_plan).fingerprint) {
        errors.push('此报价缺少仓库物流方案，请配置后重新核价')
      }
      return {
        key: `${row.sku_id}:${target.targetKey}`,
        row, target,
        name: sku?.name || row.sku || row.sku_id,
        cost: sku?.cost_cny,
        dimensions: { ...sku?.package_dimensions },
        quote, errors,
        status: !quote ? 'pending' : errors.length ? 'error' : quote.is_loss ? 'loss' : row.pricing.applied ? 'applied' : 'preview',
      }
    })
  })
}

export function skuPricingCanApply(entries: SkuPricingEntry[]): boolean {
  return entries.length > 0 && entries.every(entry => (
    entry.quote && !entry.errors.length && !entry.quote.is_loss
    && Number.isFinite(Number(pricingRecord(entry.quote.applied_price).amount))
    && Number(pricingRecord(entry.quote.applied_price).amount) > 0
  ))
}
