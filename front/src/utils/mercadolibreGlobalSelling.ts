import type { DraftDetail, MarketplaceTargetSite, UnknownRecord } from '@/types/workflow'

export interface MercadoLibreMarketplaceBinding {
  sellerId: string
  siteId: string
  logisticType: string
  businessModel: string
  pricingModel: string
  userProduct: boolean | null
}

export type MercadoLibreListingModel = 'user_products' | 'traditional_global_items'
export type MercadoLibrePricingMode = 'price' | 'net_proceeds'

export const MERCADOLIBRE_FULLY_MANAGED_BUSINESS_MODEL = 'CBT CN Fulfillment Managed'
export const MERCADOLIBRE_FULLY_MANAGED_UNSUPPORTED_MESSAGE = '该账号需走 Fully Managed/global_net_proceeds 流程，当前尚未支持。'

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

export function mercadoLibreListingModel(storeConfig: UnknownRecord): MercadoLibreListingModel | '' {
  const listingModel = text(asRecord(storeConfig.mercadolibre).listing_model).toLowerCase()
  return listingModel === 'user_products' || listingModel === 'traditional_global_items'
    ? listingModel
    : ''
}

export function mercadoLibreListingModelError(storeConfig: UnknownRecord): string {
  const rawListingModel = text(asRecord(storeConfig.mercadolibre).listing_model)
  return rawListingModel
    ? `当前店铺的 Mercado Libre listing_model 无效（${rawListingModel}），请重新验证授权并刷新账户能力。`
    : '当前店铺缺少 Mercado Libre listing_model，请重新验证授权并刷新账户能力。'
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
      userProduct: typeof record.user_product === 'boolean' ? record.user_product : null,
    }]
  })
}

export function mercadoLibreHasFullyManagedBinding(storeConfig: UnknownRecord): boolean {
  const store = asRecord(storeConfig.mercadolibre)
  if (!Array.isArray(store.marketplace_bindings)) return false
  const expected = MERCADOLIBRE_FULLY_MANAGED_BUSINESS_MODEL.toLowerCase()
  return store.marketplace_bindings.some((item) => {
    const record = asRecord(item)
    const siteId = text(record.site_id).toUpperCase()
    return Boolean(siteId && siteId !== 'CBT')
      && text(record.business_model).toLowerCase() === expected
  })
}

export function mercadoLibreUserProductBindings(storeConfig: UnknownRecord): MercadoLibreMarketplaceBinding[] {
  // Remote operation 常不返回 user_product；父账号的 user_product_seller 才是主要能力标志。
  // 后端三态契约中只有显式 false 表示该 operation 不可用于 User Products。
  return mercadoLibreMarketplaceBindings(storeConfig).filter((binding) => binding.userProduct !== false)
}

/** 当前刊登模型允许展示的 operation；listing_model 缺失或非法时 fail closed。 */
export function mercadoLibreListingBindings(storeConfig: UnknownRecord): MercadoLibreMarketplaceBinding[] {
  const listingModel = mercadoLibreListingModel(storeConfig)
  if (listingModel === 'traditional_global_items') return mercadoLibreMarketplaceBindings(storeConfig)
  if (listingModel === 'user_products') return mercadoLibreUserProductBindings(storeConfig)
  return []
}

/** 传统 Global Items 始终使用 price；User Products 才读取 binding pricing_model。 */
export function mercadoLibreBindingPricingMode(
  binding: MercadoLibreMarketplaceBinding,
  storeConfig: UnknownRecord,
): MercadoLibrePricingMode | '' {
  const listingModel = mercadoLibreListingModel(storeConfig)
  if (listingModel === 'traditional_global_items') return 'price'
  if (listingModel !== 'user_products') return ''
  const pricingModel = binding.pricingModel.trim().toLowerCase()
  return pricingModel === 'net_proceeds' || pricingModel === 'global_net_proceeds'
    ? 'net_proceeds'
    : 'price'
}

export function mercadoLibreSelectableBindings(storeConfig: UnknownRecord): MercadoLibreMarketplaceBinding[] {
  const listingModel = mercadoLibreListingModel(storeConfig)
  if (!listingModel) return []
  if (listingModel === 'user_products' && mercadoLibreHasFullyManagedBinding(storeConfig)) return []
  return mercadoLibreListingBindings(storeConfig)
}

export function mercadoLibreDestinationKey(siteId: string, logisticType: string): string {
  return `${text(siteId).toUpperCase()}:${text(logisticType).toLowerCase()}`
}

export function isMercadoLibreCbtTarget(target: MarketplaceTargetSite): boolean {
  return text(target.platform).toLowerCase() === 'mercadolibre' && text(target.site).toUpperCase() === 'CBT'
}

export function validCbtDestinationKeys(target: MarketplaceTargetSite, storeConfig: UnknownRecord): Set<string> {
  const allowed = new Set(mercadoLibreSelectableBindings(storeConfig).map((binding) => (
    mercadoLibreDestinationKey(binding.siteId, binding.logisticType)
  )))
  return new Set((target.sitesToSell || []).flatMap((destination) => {
    const key = mercadoLibreDestinationKey(destination.siteId, destination.logisticType)
    return allowed.has(key) ? [key] : []
  }))
}

export function unauthorizedCbtDestinationCount(target: MarketplaceTargetSite, storeConfig: UnknownRecord): number {
  const allowed = new Set(mercadoLibreSelectableBindings(storeConfig).map((binding) => (
    mercadoLibreDestinationKey(binding.siteId, binding.logisticType)
  )))
  const selected = new Set((target.sitesToSell || []).map((destination) => (
    mercadoLibreDestinationKey(destination.siteId, destination.logisticType)
  )))
  const invalidCount = Array.from(selected).filter((key) => !allowed.has(key)).length
  const selectedSites = new Map<string, number>()
  for (const key of selected) {
    const siteId = key.split(':', 1)[0]
    selectedSites.set(siteId, (selectedSites.get(siteId) || 0) + 1)
  }
  const duplicateOperationCount = Array.from(selectedSites.values())
    .reduce((count, operations) => count + Math.max(0, operations - 1), 0)
  return invalidCount + duplicateOperationCount
}

export function mercadoLibreTargetPricingError(target: MarketplaceTargetSite, storeConfig: UnknownRecord): string {
  const listingModel = mercadoLibreListingModel(storeConfig)
  if (!listingModel) return mercadoLibreListingModelError(storeConfig)
  const bindingByKey = new Map(mercadoLibreSelectableBindings(storeConfig).map((binding) => [
    mercadoLibreDestinationKey(binding.siteId, binding.logisticType),
    binding,
  ]))
  const selected = (target.sitesToSell || []).flatMap((destination) => {
    const binding = bindingByKey.get(mercadoLibreDestinationKey(destination.siteId, destination.logisticType))
    const mode = binding ? mercadoLibreBindingPricingMode(binding, storeConfig) : ''
    return binding && mode ? [{ destination, binding, mode }] : []
  })
  if (listingModel === 'user_products' && new Set(selected.map((item) => item.mode)).size > 1) {
    return '同一个 Siteless User Product 不能混用 price 与 net_proceeds 计价市场。'
  }
  for (const { destination, mode } of selected) {
    const siteId = text(destination.siteId).toUpperCase()
    const hasPrice = text(destination.price) !== ''
    const hasNetProceeds = text(destination.netProceeds) !== ''
    if (hasPrice && hasNetProceeds) {
      return `销售目标 ${siteId} 的 price 与 net_proceeds 互斥，只能填写一种。`
    }
    if (mode === 'net_proceeds' && hasPrice) {
      return `销售目标 ${siteId} 的账号 pricing_model=net_proceeds，不能填写 price。`
    }
    if (mode === 'net_proceeds' && !hasNetProceeds) {
      return `销售目标 ${siteId} 的账号 pricing_model=net_proceeds，必须填写 Net proceeds。`
    }
    if (mode === 'price' && hasNetProceeds) {
      return `销售目标 ${siteId} 未启用 net_proceeds 计价，必须使用市场售价。`
    }
  }
  return ''
}

export function cbtDestinationSelectionReady(draft: DraftDetail, storeConfig: UnknownRecord): boolean {
  const cbtTargets = draft.targetSites.filter(isMercadoLibreCbtTarget)
  if (!cbtTargets.length) return true
  if (mercadoLibreAccountSiteId(storeConfig) !== 'CBT') return false
  const listingModel = mercadoLibreListingModel(storeConfig)
  if (!listingModel) return false
  if (listingModel === 'user_products' && mercadoLibreHasFullyManagedBinding(storeConfig)) return false
  return cbtTargets.every((target) => (
    validCbtDestinationKeys(target, storeConfig).size > 0
    && unauthorizedCbtDestinationCount(target, storeConfig) === 0
  ))
}
