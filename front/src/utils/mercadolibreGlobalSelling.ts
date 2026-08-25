import type { DraftDetail, MarketplaceTargetSite, UnknownRecord } from '@/types/workflow'

export interface MercadoLibreMarketplaceBinding {
  sellerId: string
  siteId: string
  logisticType: string
  businessModel: string
  pricingModel: string
  userProduct: boolean
}

export const MERCADOLIBRE_FULLY_MANAGED_BUSINESS_MODEL = 'CBT CN Fulfillment Managed'

function asRecord(value: unknown): UnknownRecord {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as UnknownRecord
    : {}
}

function text(value: unknown): string {
  return String(value ?? '').trim()
}

export function mercadoLibreAccountSiteId(storeConfig: UnknownRecord): string {
  return text(asRecord(storeConfig.mercadolibre).account_site_id).toUpperCase()
}

export function mercadoLibreMarketplaceBindings(storeConfig: UnknownRecord): MercadoLibreMarketplaceBinding[] {
  const store = asRecord(storeConfig.mercadolibre)
  if (!Array.isArray(store.marketplace_bindings)) return []
  const seen = new Set<string>()
  return store.marketplace_bindings.flatMap((item) => {
    const record = asRecord(item)
    const siteId = text(record.site_id).toUpperCase()
    const logisticType = text(record.logistic_type).toLowerCase()
    if (!siteId || siteId === 'CBT' || !logisticType) return []
    const key = mercadoLibreDestinationKey(siteId, logisticType)
    if (seen.has(key)) return []
    seen.add(key)
    return [{
      sellerId: text(record.seller_id),
      siteId,
      logisticType,
      businessModel: text(record.business_model),
      pricingModel: text(record.pricing_model),
      userProduct: record.user_product === true,
    }]
  })
}

export function mercadoLibreIsFullyManaged(storeConfig: UnknownRecord): boolean {
  const store = asRecord(storeConfig.mercadolibre)
  if (!Array.isArray(store.marketplace_bindings)) return false
  const expected = MERCADOLIBRE_FULLY_MANAGED_BUSINESS_MODEL.toLowerCase()
  return store.marketplace_bindings.some((item) => (
    text(asRecord(item).business_model).toLowerCase() === expected
  ))
}

export function mercadoLibreDestinationKey(siteId: string, logisticType: string): string {
  return `${text(siteId).toUpperCase()}:${text(logisticType).toLowerCase()}`
}

export function isMercadoLibreCbtTarget(target: MarketplaceTargetSite): boolean {
  return text(target.platform).toLowerCase() === 'mercadolibre' && text(target.site).toUpperCase() === 'CBT'
}

export function validCbtDestinationKeys(target: MarketplaceTargetSite, storeConfig: UnknownRecord): Set<string> {
  const allowed = new Set(mercadoLibreMarketplaceBindings(storeConfig).map((binding) => (
    mercadoLibreDestinationKey(binding.siteId, binding.logisticType)
  )))
  return new Set((target.sitesToSell || []).flatMap((destination) => {
    const key = mercadoLibreDestinationKey(destination.siteId, destination.logisticType)
    return allowed.has(key) ? [key] : []
  }))
}

export function unauthorizedCbtDestinationCount(target: MarketplaceTargetSite, storeConfig: UnknownRecord): number {
  const allowed = new Set(mercadoLibreMarketplaceBindings(storeConfig).map((binding) => (
    mercadoLibreDestinationKey(binding.siteId, binding.logisticType)
  )))
  const selected = new Set((target.sitesToSell || []).map((destination) => (
    mercadoLibreDestinationKey(destination.siteId, destination.logisticType)
  )))
  return Array.from(selected).filter((key) => !allowed.has(key)).length
}

export function cbtDestinationSelectionReady(draft: DraftDetail, storeConfig: UnknownRecord): boolean {
  const cbtTargets = draft.targetSites.filter(isMercadoLibreCbtTarget)
  if (!cbtTargets.length) return true
  if (mercadoLibreAccountSiteId(storeConfig) !== 'CBT') return false
  if (mercadoLibreIsFullyManaged(storeConfig)) return false
  return cbtTargets.every((target) => (
    validCbtDestinationKeys(target, storeConfig).size > 0
    && unauthorizedCbtDestinationCount(target, storeConfig) === 0
  ))
}
