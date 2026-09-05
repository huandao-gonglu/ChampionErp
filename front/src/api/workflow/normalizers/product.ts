import { createEmptyDraft, createEmptyProduct } from '@/constants/initialState'
import { listingLanguageValue } from '@/constants/locales'
import type { BackendProduct, BackendProductSource } from '@/types/workflow.generated'
import type {

  DraftDetail,
  DraftProductContext,
  ImageAsset,
  Marketplace,
  MercadoLibreMarketPublication,
  MercadoLibrePublication,
  MarketplaceDraft,
  Product,
  ProductSku,
  DraftSku,
  UnknownRecord,
} from '@/types/workflow'

import {
  PRODUCT_SCHEMA_VERSION,
  asRecord,
  isRecord,
  getString,
  getBoolean,
  wireStringList,
  platformList,
  normalizeAttributes,
  toBackendAttributes,
  normalizeValidationErrors,
  normalizeTargetSites,
  normalizeDimensions,
  normalizeImageAsset,
  normalizeDraftImageRefs,
  toBackendDraftImageRef,
  toBackendTargetSite,
} from './core'

function nullablePrice(value: unknown): number | string | null {
  if (value === null || value === undefined || value === '') return null
  if (typeof value === 'number') return Number.isFinite(value) ? value : null
  return typeof value === 'string' ? value.trim() || null : null
}

function publicationError(value: unknown): string | UnknownRecord | unknown[] {
  if (typeof value === 'string') return value
  if (Array.isArray(value)) {
    return value.map((item) => isRecord(item) ? { ...asRecord(item) } : item)
  }
  return isRecord(value) ? { ...asRecord(value) } : ''
}

export function normalizeMercadoLibrePublication(value: unknown): MercadoLibrePublication | null {
  if (!isRecord(value)) return null
  const record = asRecord(value)
  const model = getString(record, ['model'])
  if (model !== 'user_products' && model !== 'traditional_global_items') return null
  const seenSites = new Set<string>()
  const markets = (Array.isArray(record.markets) ? record.markets : []).flatMap((item): MercadoLibreMarketPublication[] => {
    const market = asRecord(item)
    const siteId = getString(market, ['site_id']).toUpperCase()
    if (!siteId || seenSites.has(siteId)) return []
    seenSites.add(siteId)
    return [{
      siteId,
      itemId: getString(market, ['item_id']),
      userProductId: getString(market, ['user_product_id']),
      sellerId: getString(market, ['seller_id']),
      logisticType: getString(market, ['logistic_type']).toLowerCase(),
      status: getString(market, ['status']),
      price: nullablePrice(market.price),
      netProceeds: nullablePrice(market.net_proceeds),
      freeShipping: typeof market.free_shipping === 'boolean' ? market.free_shipping : null,
      saleTerms: (Array.isArray(market.sale_terms) ? market.sale_terms : [])
        .filter((term) => isRecord(term))
        .map((term) => ({ ...asRecord(term) })),
      currencyId: getString(market, ['currency_id']),
      listingTypeId: getString(market, ['listing_type_id']),
      error: publicationError(market.error),
      lastOperation: isRecord(market.last_operation) ? { ...asRecord(market.last_operation) } : {},
      updatedAt: getString(market, ['updated_at']),
    }]
  })
  const publication: MercadoLibrePublication = {
    model,
    accountUserId: getString(record, ['account_user_id']),
    sitelessUserProductId: getString(record, ['siteless_user_product_id']),
    sitelessFamilyId: getString(record, ['siteless_family_id']),
    parentItemId: getString(record, ['parent_item_id']),
    parentUserProductId: getString(record, ['parent_user_product_id']),
    sellerId: getString(record, ['seller_id']),
    status: getString(record, ['status']),
    familyName: getString(record, ['family_name']),
    markets,
    confirmedPayload: isRecord(record.confirmed_payload) ? { ...asRecord(record.confirmed_payload) } : {},
    error: publicationError(record.error),
    lastOperation: isRecord(record.last_operation) ? { ...asRecord(record.last_operation) } : {},
    updatedAt: getString(record, ['updated_at']),
  }
  if (model === 'traditional_global_items') {
    return publication.parentItemId ? publication : null
  }
  return publication.sitelessUserProductId || publication.markets.length ? publication : null
}

export function toBackendMercadoLibrePublication(publication: MercadoLibrePublication): UnknownRecord {
  return {
    model: publication.model,
    account_user_id: publication.accountUserId,
    siteless_user_product_id: publication.sitelessUserProductId,
    siteless_family_id: publication.sitelessFamilyId,
    parent_item_id: publication.parentItemId,
    parent_user_product_id: publication.parentUserProductId,
    seller_id: publication.sellerId,
    status: publication.status,
    family_name: publication.familyName,
    markets: publication.markets.map((market) => ({
      site_id: market.siteId,
      item_id: market.itemId,
      user_product_id: market.userProductId,
      seller_id: market.sellerId,
      logistic_type: market.logisticType,
      status: market.status,
      price: market.price,
      net_proceeds: market.netProceeds,
      free_shipping: market.freeShipping,
      sale_terms: market.saleTerms.map((term) => ({ ...term })),
      currency_id: market.currencyId,
      listing_type_id: market.listingTypeId,
      error: market.error,
      last_operation: { ...market.lastOperation },
      updated_at: market.updatedAt,
    })),
    confirmed_payload: { ...publication.confirmedPayload },
    error: publication.error,
    last_operation: { ...publication.lastOperation },
    updated_at: publication.updatedAt,
  }
}

const REMOVED_PRODUCT_FIELDS = [
  'id',
  'productId',
  'title',
  'product_name',
  'category_name',
  'category_id',
  'yandex_category_id',
  'ozon_category_id',
  'source_url',
  'source_platform',
  'source_images',
  'sourceImages',
  'source_image_urls',
  'generated_images',
  'image_pool',
  'detected_price',
  'detected_currency',
  'source_price_cny',
  'source_price_cny_for_cost',
  'source_text',
  'source_material',
  'sellingPoints',
  'packageIncludes',
  'publish_logs',
] as const

const REMOVED_PRODUCT_SOURCE_FIELDS = [
  'sourceUrl',
  'sourcePlatform',
  'imagePool',
  'weightKg',
  'attributeMatches',
] as const

function assertCurrentProductWireSchema(record: UnknownRecord): void {
  if (record.schema_version !== PRODUCT_SCHEMA_VERSION) {
    const received = record.schema_version === undefined ? '未声明' : String(record.schema_version)
    throw new Error(`不支持的商品数据 schema_version：${received}，当前仅接受 ${PRODUCT_SCHEMA_VERSION}`)
  }
  const source = asRecord(record.source)
  const removedField = REMOVED_PRODUCT_FIELDS.find((key) => Object.prototype.hasOwnProperty.call(record, key))
    || REMOVED_PRODUCT_SOURCE_FIELDS.find((key) => Object.prototype.hasOwnProperty.call(source, key))
  if (removedField) {
    throw new Error(`商品数据包含已移除的旧字段：${removedField}`)
  }
  if (!Object.prototype.hasOwnProperty.call(record, 'product_id') || !isRecord(record.source)) {
    throw new Error('商品数据不符合当前 wire schema：必须包含 product_id 和 source')
  }
}

export function normalizeDraft(value: unknown, language: string): MarketplaceDraft {
  const record = asRecord(value)
  const draft = createEmptyDraft(language)
  const packageDimensions = asRecord(record.package_dimensions)
  const saleTerms = Array.isArray(record.sale_terms)
    ? record.sale_terms.map((item) => asRecord(item))
    : []
  const site = getString(record, ['site'], draft.site)
  const draftLanguage = getString(record, ['language'], language)
  const categoryId = getString(record, ['category_id'])
  const descriptionCategoryId = getString(record, ['description_category_id'])
  const categoryPath = getString(record, ['category_path'])
  const attributes = normalizeAttributes(record.attributes)
  const validationErrors = normalizeValidationErrors(record.validation_errors)
  return {
    ...draft,
    skuItems: (Array.isArray(record.sku_items) ? record.sku_items : []) as DraftSku[],
    grouping: { mode: String(asRecord(record.grouping).mode || "combined"), name: String(asRecord(record.grouping).name || "") },
    draftId: getString(record, ['draft_id']),
    platforms: platformList(record.platforms),
    targetSites: normalizeTargetSites(record.target_sites, platformList(record.platforms)[0] || 'mercadolibre', site),
    site,
    enabled: getBoolean(record, ['enabled'], draft.enabled),
    globalTitle: getString(record, ['global_title']),
    title: getString(record, ['title']),
    description: getString(record, ['description']),
    brand: getString(record, ['brand']),
    model: getString(record, ['model']),
    bullets: wireStringList(record.bullets),
    categoryId,
    descriptionCategoryId,
    categoryPath,
    attributes,
    pricing: asRecord(record.pricing),
    images: normalizeDraftImageRefs(record.images),
    status: getString(record, ['status'], draft.status) as MarketplaceDraft['status'],
    language: draftLanguage,
    stock: getString(record, ['stock']),
    sku: getString(record, ['sku']),
    upc: getString(record, ['upc']),
    packageDimensions: {
      lengthCm: getString(packageDimensions, ['length_cm']),
      widthCm: getString(packageDimensions, ['width_cm']),
      heightCm: getString(packageDimensions, ['height_cm']),
      weightKg: getString(packageDimensions, ['weight_kg']),
    },
    saleTerms,
    allowGtinExemption: getBoolean(record, ['allow_gtin_exemption']),
    validationErrors,
    publishStatus: getString(record, ['publish_status']),
    lastPrecheck: asRecord(record.last_precheck),
    lastPrecheckTarget: asRecord(record.last_precheck_target),
    publication: normalizeMercadoLibrePublication(record.publication),
  }
}

export function normalizeBackendProduct(value: unknown, imagePoolOverride?: unknown): Product {
  const record = asRecord(value)
  assertCurrentProductWireSchema(record)
  const source = asRecord(record.source)
  const drafts = asRecord(record.drafts)
  const imagePoolRaw = Array.isArray(imagePoolOverride)
    ? imagePoolOverride
    : Array.isArray(source.image_pool)
      ? source.image_pool
      : []
  const dimensions = normalizeDimensions(source.dimensions)
  const product = createEmptyProduct()
  return {
    ...product,
    skuItems: (Array.isArray(record.sku_items) ? record.sku_items : []) as ProductSku[],
    productId: getString(record, ['product_id']),
    name: getString(record, ['name']),
    brand: getString(record, ['brand']),
    model: getString(record, ['model']),
    category: getString(record, ['category']),
    sku: getString(record, ['sku']),
    stock: getString(record, ['stock']),
    upc: getString(record, ['upc']),
    cost: getString(record, ['cost']),
    materials: wireStringList(record.materials),
    sellingPoints: wireStringList(record.selling_points),
    packageIncludes: wireStringList(record.package_includes),
    attributes: { ...asRecord(record.attributes) },
    source: {
      sourceUrl: getString(source, ['source_url']),
      sourcePlatform: getString(source, ['source_platform']),
      title: getString(source, ['title']),
      price: getString(source, ['price']),
      currency: getString(source, ['currency']),
      description: getString(source, ['description']),
      dimensions,
      weightKg: getString(source, ['weight_kg']),
      imagePool: imagePoolRaw.map(normalizeImageAsset),
      attributes: { ...asRecord(source.attributes) },
      attributeMatches: asRecord(source.attribute_matches),
      collectStatus: getString(source, ['collect_status']),
      collectDiagnostics: asRecord(source.collect_diagnostics),
    },
    drafts: {
      mercadolibre: normalizeDraft(drafts.mercadolibre, listingLanguageValue('mercadolibre')),
      yandex: normalizeDraft(drafts.yandex, listingLanguageValue('yandex')),
      ozon: normalizeDraft(drafts.ozon, listingLanguageValue('ozon')),
    },
    raw: record,
  }
}

export function toBackendImageAsset(image: ImageAsset): UnknownRecord {
  return {
    id: image.id,
    url: image.url,
    path: image.path,
    preview_url: image.previewUrl,
    origin: image.origin,
    usage: image.usage,
    platforms: image.platforms,
    is_main: image.isMain,
    selected: image.selected,
    status: image.status,
    width: image.width,
    height: image.height,
    target_language: image.targetLanguage,
    derived_from_id: image.derivedFromId,
    provider: image.provider,
    storage_key: image.storageKey,
    content_sha256: image.contentSha256,
    delivery_provider: image.deliveryProvider,
    delivery_error: image.deliveryError,
  }
}

export function toBackendDraft(draft: MarketplaceDraft): UnknownRecord {
  return {
    sku_items: draft.skuItems,
    grouping: draft.grouping,
    enabled: draft.enabled,
    draft_id: draft.draftId,
    platforms: draft.platforms,
    target_sites: draft.targetSites.map(toBackendTargetSite),
    site: draft.site,
    global_title: draft.globalTitle,
    title: draft.title,
    description: draft.description,
    brand: draft.brand,
    model: draft.model,
    bullets: draft.bullets,
    category_id: draft.categoryId,
    description_category_id: draft.descriptionCategoryId,
    category_path: draft.categoryPath,
    attributes: toBackendAttributes(draft.attributes),
    pricing: draft.pricing,
    images: draft.images.map(toBackendDraftImageRef),
    status: draft.status,
    language: draft.language,
    stock: draft.stock,
    sku: draft.sku,
    upc: draft.upc,
    package_dimensions: {
      length_cm: draft.packageDimensions.lengthCm,
      width_cm: draft.packageDimensions.widthCm,
      height_cm: draft.packageDimensions.heightCm,
      weight_kg: draft.packageDimensions.weightKg,
    },
    sale_terms: draft.saleTerms,
    allow_gtin_exemption: draft.allowGtinExemption,
    validation_errors: draft.validationErrors,
    publish_status: draft.publishStatus,
    last_precheck: draft.lastPrecheck,
    last_precheck_target: draft.lastPrecheckTarget,
    publication: draft.publication ? toBackendMercadoLibrePublication(draft.publication) : null,
  }
}

export function normalizeDraftDetail(value: unknown): DraftDetail {
  const record = asRecord(value)
  const platform = (getString(record, ['platform']) || 'mercadolibre') as Marketplace
  const draft = normalizeDraft(record, listingLanguageValue(platform))
  const platforms = draft.platforms.length ? draft.platforms : [platform]
  const primaryPlatform = platforms.includes(platform) ? platform : platforms[0] || platform
  return {
    ...draft,
    productId: getString(record, ['product_id']),
    sourceProductId: getString(record, ['source_product_id']),
    platform: primaryPlatform,
    platforms,
    targetSites: normalizeTargetSites(record.target_sites, primaryPlatform, getString(record, ['site'])),
    site: getString(record, ['site']),
    createdAt: getString(record, ['created_at']),
    updatedAt: getString(record, ['updated_at']),
    raw: record,
  }
}

export function normalizeDraftProductContext(value: unknown): DraftProductContext {
  const record = asRecord(value)
  const dimensions = normalizeDimensions(record.dimensions)
  const imagePoolRaw = Array.isArray(record.image_pool)
    ? record.image_pool
    : []
  return {
    skuItems: (Array.isArray(record.sku_items) ? record.sku_items : []) as ProductSku[],
    productId: getString(record, ['product_id']),
    sourceProductId: getString(record, ['source_product_id']),
    title: getString(record, ['title']),
    sourceTitle: getString(record, ['source_title']),
    sourcePlatform: getString(record, ['source_platform']),
    sourceUrl: getString(record, ['source_url']),
    brand: getString(record, ['brand']),
    model: getString(record, ['model']),
    sku: getString(record, ['sku']),
    stock: getString(record, ['stock']),
    cost: getString(record, ['cost']),
    sourcePrice: getString(record, ['source_price']),
    currency: getString(record, ['currency']),
    weightKg: getString(record, ['weight_kg']),
    dimensions,
    imagePool: imagePoolRaw.map(normalizeImageAsset),
    raw: record,
  }
}

export function toBackendDraftDetail(draft: DraftDetail): UnknownRecord {
  const platforms = draft.platforms.length ? draft.platforms : [draft.platform]
  const platform = platforms.includes(draft.platform) ? draft.platform : platforms[0] || draft.platform
  return {
    ...toBackendDraft(draft),
    draft_id: draft.draftId,
    product_id: draft.productId,
    source_product_id: draft.sourceProductId || draft.productId,
    platform,
    platforms,
    target_sites: draft.targetSites.map(toBackendTargetSite),
    site: draft.site,
  }
}

export function toBackendProduct(product: Product): BackendProduct {
  const rawProduct = asRecord(product.raw)
  const rawSource = asRecord(rawProduct.source)
  const recordList = (value: unknown) => (
    Array.isArray(value) ? value.map(asRecord).filter((item) => Object.keys(item).length > 0) : []
  )
  const canonicalSource: BackendProductSource = {
    material: getString(rawSource, ['material']),
    package_contents: product.packageIncludes,
    variants: recordList(rawSource.variants),
    skus: recordList(rawSource.skus),
    attribute_matches: product.source.attributeMatches,
    collect_logs: Array.isArray(rawSource.collect_logs) ? rawSource.collect_logs : [],
    brand: getString(rawSource, ['brand']),
    model: getString(rawSource, ['model']),
    sku: getString(rawSource, ['sku']),
    created_at: getString(rawSource, ['created_at']),
  }
  return {
    schema_version: PRODUCT_SCHEMA_VERSION,
    product_id: product.productId,
    name: product.name,
    brand: product.brand,
    model: product.model,
    category: product.category,
    sku: product.sku,
    stock: product.stock,
    upc: product.upc,
    cost: product.cost,
    materials: product.materials,
    selling_points: product.sellingPoints,
    package_includes: product.packageIncludes,
    target_customer: getString(rawProduct, ['target_customer']),
    colors: wireStringList(rawProduct.colors),
    avoid_claims: wireStringList(rawProduct.avoid_claims),
    description: getString(rawProduct, ['description']),
    dimensions: typeof rawProduct.dimensions === 'string' ? rawProduct.dimensions.trim() : '',
    weight_kg: getString(rawProduct, ['weight_kg']),
    marketplace_terms: asRecord(rawProduct.marketplace_terms),
    attributes: product.attributes,
    listing_overrides: asRecord(rawProduct.listing_overrides),
    copy_results: asRecord(rawProduct.copy_results),
    sku_items: product.skuItems,
    pricing_defaults: asRecord(rawProduct.pricing_defaults),
    publish_preview: asRecord(rawProduct.publish_preview),
    collect_status: getString(rawProduct, ['collect_status']),
    collect_logs: Array.isArray(rawProduct.collect_logs) ? rawProduct.collect_logs : [],
    workflow_statuses: Object.fromEntries(
      Object.entries(asRecord(rawProduct.workflow_statuses)).map(([key, value]) => [key, String(value ?? '')]),
    ),
    created_at: getString(rawProduct, ['created_at']),
    updated_at: getString(rawProduct, ['updated_at']),
    source: {
      ...canonicalSource,
      source_url: product.source.sourceUrl,
      source_platform: product.source.sourcePlatform,
      title: product.source.title,
      price: product.source.price,
      currency: product.source.currency,
      description: product.source.description,
      bullets: product.sellingPoints,
      dimensions: {
        length_cm: product.source.dimensions.lengthCm,
        width_cm: product.source.dimensions.widthCm,
        height_cm: product.source.dimensions.heightCm,
      },
      weight_kg: product.source.weightKg,
      image_pool: product.source.imagePool.map(toBackendImageAsset),
      attributes: product.source.attributes,
      attribute_matches: product.source.attributeMatches,
      collect_status: product.source.collectStatus,
      collect_diagnostics: product.source.collectDiagnostics,
    },
  }
}
