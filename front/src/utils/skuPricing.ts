import type { DraftSku, PricingTargetInput, ProductSku, UnknownRecord } from '@/types/workflow'

export function pricingRecord(value: unknown): UnknownRecord {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as UnknownRecord : {}
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
      const errors = (Array.isArray(quote?.errors) ? quote.errors : []).map(error => (
        typeof error === 'string' ? error : String(pricingRecord(error).message || pricingRecord(error).field || '')
      )).filter(Boolean)
      return {
        key: `${row.sku_id}:${target.targetKey}`,
        row, target,
        name: sku?.name || row.sku || row.sku_id,
        cost: row.overrides.cost_cny ?? sku?.cost_cny,
        dimensions: { ...sku?.package_dimensions, ...pricingRecord(row.overrides.package_dimensions) },
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
