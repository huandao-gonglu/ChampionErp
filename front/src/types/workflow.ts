export type Marketplace = string

export interface MarketplaceSiteOption {
  key: string
  code: string
  label: string
  language: string
  currency: string
}

export interface MarketplaceOption {
  key: Marketplace
  label: string
  sites: MarketplaceSiteOption[]
}

export interface MarketplaceTargetSite {
  platform: Marketplace
  site: string
  language: string
  currency: string
}

export type WorkflowStatus =
  | 'pending'
  | 'collected'
  | 'claimed'
  | 'copy_ready'
  | 'images_ready'
  | 'priced'
  | 'category_ready'
  | 'ready_to_publish'
  | 'published'
  | 'failed'

export type CollectMode = 'browser' | 'http' | 'manual' | 'extension' | 'api'

export type UnknownRecord = Record<string, unknown>

export interface ImageAsset {
  id: string
  url: string
  path: string
  previewUrl: string
  origin: string
  usage: string
  platforms: Marketplace[]
  isMain: boolean
  selected: boolean
  status: string
  width: number
  height: number
  targetLanguage?: string
  derivedFromId?: string
  provider?: string
}

export type DraftImageRole = 'main' | 'detail' | 'size' | 'scene' | 'package' | 'selling_point' | 'material' | 'other'

export interface DraftImageRef {
  assetId: string
  role: DraftImageRole
  order: number
  label?: string
  note?: string
  altText?: string
  sourceAssetId?: string
}

export interface ProductSource {
  sourceUrl: string
  sourcePlatform: string
  title: string
  price: string
  currency: string
  description: string
  dimensions: {
    lengthCm: string
    widthCm: string
    heightCm: string
  }
  weightKg: string
  imagePool: ImageAsset[]
  attributes: Record<string, string>
  collectStatus: string
  collectDiagnostics: UnknownRecord
}

export interface MarketplaceDraft {
  draftId: string
  platforms: Marketplace[]
  targetSites: MarketplaceTargetSite[]
  site: string
  currency: string
  enabled: boolean
  title: string
  description: string
  bullets: string[]
  categoryId: string
  categoryPath: string
  attributes: Record<string, string>
  price: string
  pricing: UnknownRecord
  images: DraftImageRef[]
  status: WorkflowStatus
  language: string
  stock: string
  sku: string
  upc: string
  packageDimensions: {
    lengthCm: string
    widthCm: string
    heightCm: string
    weightKg: string
  }
  saleTerms: UnknownRecord[]
  allowGtinExemption: boolean
  validationErrors: Array<UnknownRecord | string>
}

export interface Product {
  productId: string
  name: string
  brand: string
  model: string
  category: string
  sku: string
  stock: string
  upc: string
  cost: string
  materials: string[]
  sellingPoints: string[]
  packageIncludes: string[]
  source: ProductSource
  drafts: Record<Marketplace, MarketplaceDraft>
  raw: UnknownRecord
}

export interface DraftDetail extends MarketplaceDraft {
  productId: string
  sourceProductId: string
  platform: Marketplace
  site: string
  createdAt: string
  updatedAt: string
  raw: UnknownRecord
}

export interface DraftProductContext {
  productId: string
  sourceProductId: string
  title: string
  sourceTitle: string
  sourcePlatform: string
  sourceUrl: string
  brand: string
  model: string
  sku: string
  stock: string
  cost: string
  sourcePrice: string
  currency: string
  weightKg: string
  dimensions: {
    lengthCm: string
    widthCm: string
    heightCm: string
  }
  imagePool: ImageAsset[]
  raw: UnknownRecord
}

export interface PricingInput {
  platform: Marketplace
  site: string
  purchaseCostCny: number
  domesticFreightCny: number
  weightKg: number
  lengthCm: number
  widthCm: number
  heightCm: number
  commissionPercent: number
  targetMarginPercent: number
  usdCnyRate: number
  mxnUsdRate: number
  rubCnyRate: number
  exchangeRateMode: 'live' | 'manual'
  displayCurrencyMode: 'platform' | 'cny'
  targets: PricingTargetInput[]
}

export interface PricingTargetInput {
  targetKey: string
  platform: Marketplace
  site: string
  currency: string
  commissionPercent: number
  paymentFeePercent: number
  targetMarginPercent: number
  shippingCostUsd: number
  shippingCostCny: number
  russiaFreightRate: number
  appliedPrice: number
}

export interface PricingTargetResult {
  targetKey: string
  platform: Marketplace
  site: string
  currency: string
  suggestedPrice: number
  suggestedPriceUsd: number
  suggestedPriceCny: number
  appliedPrice: number
  shippingCostUsd: number
  shippingCostCny: number
  totalCostCny: number
  netRevenueCny: number
  profitCny: number
  marginPercent: number
  commissionPercent: number
  paymentFeePercent: number
  targetMarginPercent: number
  usdCnyRate: number
  mxnUsdRate: number
  rubCnyRate: number
  isLoss: boolean
  errors: Array<UnknownRecord | string>
  raw: UnknownRecord
}

export interface PricingResult {
  results: PricingTargetResult[]
  suggestedPriceMxn: number
  suggestedPriceUsd: number
  suggestedPriceCny: number
  wbPriceRub: number
  shippingCostUsd: number
  shippingCostCny: number
  totalCostCny: number
  netRevenueCny: number
  profitCny: number
  marginPercent: number
  usdCnyRate: number
  mxnUsdRate: number
  rubUsdRate: number
  rubCnyRate: number
  exchangeRateMode: string
  exchangeRateSource: string
  exchangeRateFetchedAt: string
  exchangeRateCached: boolean
}

export interface CategorySelection {
  platform: Marketplace
  categoryId: string
  categoryPath: string
  requiredAttributes: Array<{ id: string; name: string; required: boolean; options?: string[] }>
  optionalAttributes: Array<{ id: string; name: string; required: boolean; options?: string[] }>
}

export interface CategoryAttributeTranslation {
  label: string
  help: string
  values: Record<string, string>
}

export type CategoryAttributeTranslations = Record<string, CategoryAttributeTranslation>

export type CategoryResultTranslations = Record<string, string>

export interface CategorySearchResult {
  id: string
  name: string
  path: string
  raw: UnknownRecord
}

export interface CategoryPrecheckResult {
  ok: boolean
  errors: string[]
  missingFields: string[]
  checkedAt: string
  raw: UnknownRecord
}

export interface PrecheckIssue {
  code: string
  field: string
  message: string
  severity: 'error' | 'warning' | string
  nextAction: string
}

export interface PublishPrecheck {
  ok: boolean
  errors: string[]
  warnings: string[]
  errorItems: PrecheckIssue[]
  warningItems: PrecheckIssue[]
  checkedAt: string
}

export interface PublishJob {
  jobId: string
  status: 'queued' | 'running' | 'completed' | 'failed'
  platforms: Marketplace[]
  createdAt: string
}

export interface CollectForm {
  productUrl: string
  productUrls: string
  platform: string
  mode: CollectMode
  alibabaCookie: string
  alibabaAppKey: string
  alibabaAppSecret: string
  alibabaAccessToken: string
  alibabaApiBaseUrl: string
  alibabaApiMethod: string
  alibabaApiVersion: string
  alibabaApiTimeoutSeconds: string
  autoAiRecognition: boolean
  outputDir: string
  selectedClaimPlatforms: Marketplace[]
  manualTitle: string
  manualPrice: string
  manualBullets: string
  manualDescription: string
  manualDimensions: string
  manualWeight: string
  manualImages: string
  rawText: string
}

export interface CollectDiagnostics {
  status: 'idle' | 'running' | 'success' | 'failed'
  progress: number
  message: string
  downloadedImages: number
  extractedBullets: number
  antiBotWarning: boolean
  lastSourceUrl: string
  errorCode: string
  nextAction: string
  htmlSnapshotPath: string
  screenshotPath: string
  raw: UnknownRecord
}

export interface CollectBatchRow {
  url: string
  platform: string
  status: string
  ok: boolean
  title: string
  image: string
  error: string
  errorCode: string
  nextAction: string
  productId: string
  product?: Product
}

export interface BrowserDebugTab {
  platformDetected: string
  title: string
  url: string
}

export interface BrowserDebugStatus {
  connected: boolean
  port: number
  tabsCount: number
  tabs: BrowserDebugTab[]
  errorCode: string
  errorMessage: string
  nextAction: string
  powershellCommand: string
  cmdCommand: string
  profileDir: string
}

export interface ProductIndexItem {
  productId: string
  title: string
  mainImage: string
  sourcePlatform: string
  sourceUrl: string
  createdAt: string
  updatedAt: string
  platforms: Marketplace[]
  draftStatuses: Partial<Record<Marketplace, WorkflowStatus>>
  productFilePath: string
  collectStatus: string
  workflowStatus: string
  aiCopyStatus: string
  imageStatus: string
  categoryStatus: string
  attributesStatus: string
  pricingStatus: string
  precheckStatus: string
  publishStatus: string
  publishQueueReady: boolean
  optimized: boolean
  raw: UnknownRecord
}

export interface DraftIndexItem {
  draftId: string
  productId: string
  sourceProductId: string
  platform: Marketplace
  platforms: Marketplace[]
  targetSites: MarketplaceTargetSite[]
  site: string
  language: string
  status: WorkflowStatus | string
  title: string
  productTitle: string
  mainImage: string
  sourcePlatform: string
  sourceUrl: string
  categoryId: string
  categoryPath: string
  price: string
  publishStatus: string
  createdAt: string
  updatedAt: string
  productFilePath: string
  raw: UnknownRecord
}

export interface PublishLogItem {
  jobId: string
  productId: string
  platform: string
  status: string
  startedAt: string
  finishedAt: string
  errorCode: string
  errorMessage: string
  requestPayloadPath: string
  responseBodyPath: string
  raw: UnknownRecord
}

export interface MercadoLibreRemoteItem {
  id: string
  title: string
  status: string
  subStatus: string[]
  permalink: string
  thumbnail: string
  price: number
  currencyId: string
  availableQuantity: number
  soldQuantity: number
  categoryId: string
  listingTypeId: string
  sellerSku: string
  dateCreated: string
  lastUpdated: string
  raw: UnknownRecord
}

export interface MercadoLibreOrderLine {
  itemId: string
  title: string
  sellerSku: string
  quantity: string
}

export interface MercadoLibreOrderItem {
  id: string
  status: string
  statusDetail: string
  dateCreated: string
  dateClosed: string
  lastUpdated: string
  totalAmount: number
  paidAmount: number
  currencyId: string
  buyerId: string
  buyerNickname: string
  shippingId: string
  shippingStatus: string
  paymentStatuses: string[]
  items: MercadoLibreOrderLine[]
  itemTitles: string[]
  itemIds: string[]
  raw: UnknownRecord
}

export interface MercadoLibreOrderNotification {
  topic: string
  resource: string
  userId: string
  applicationId: string
  attempts: number
  sent: string
  receivedAt: string
  orderId: string
  error: string
  raw: UnknownRecord
}

export interface MercadoLibreOrdersPage {
  items: MercadoLibreOrderItem[]
  notifications: MercadoLibreOrderNotification[]
  total: number
  checkedAt: string
}

export interface MercadoLibreRemotePagination {
  page: number
  perPage: number
  offset: number
  total: number
  totalPages: number
  hasPrev: boolean
  hasNext: boolean
}

export interface MercadoLibrePublishedPage {
  items: MercadoLibreRemoteItem[]
  pagination: MercadoLibreRemotePagination
}

export type ProductResearchSearchMode = 'target_only' | 'target_plus_reference' | 'global_scan'

export interface ProductResearchPrice {
  amount: number
  currency: string
}

export interface HotProductCandidate {
  id: string
  title: string
  imageUrl: string
  rank: number
  sourceUrl: string
  marketId: string
  platform: string
  site: string
  keyword: string
  price?: ProductResearchPrice
  rating: number
  reviewCount: number
  hotScore: number
  sourceName: string
  collectedAt: string
  raw: UnknownRecord
}

export interface ProductResearchSourceStatus {
  source: string
  sourceId: string
  market: string
  status: string
  itemsFound: number
  errorMessage: string
  providerStrategy: string
  rawItemsFound: number
  itemsFiltered: number
  diagnosticMessage: string
  raw: UnknownRecord
}

export interface ProductResearchRunSummary {
  runId: string
  status: string
  searchMode: string
  createdAt: string
  completedAt: string
  expiresAt: string
  description: string
  progressDescription: string
  request: UnknownRecord
  raw: UnknownRecord
}

export interface ProductResearchResponse {
  run: ProductResearchRunSummary
  items: HotProductCandidate[]
  sourceStatus: ProductResearchSourceStatus[]
  description: string
  raw: UnknownRecord
}

export interface ProductResearchSourceRegistryItem {
  id: string
  name: string
  sourceType: string
  platform: string
  enabled: boolean
  priority: number
  supportedMarkets: string[]
  supportedLanguages: string[]
  supportedDataTypes: string[]
  authRequired: boolean
  rateLimitPerMinute: number
  complianceNote: string
  providerStrategy: string
  configJson: UnknownRecord
  raw: UnknownRecord
}

export interface ProductResearchMarketSearchMethodBinding {
  methodId: string
  enabled: boolean
  prompt: string
  configJson: UnknownRecord
  raw: UnknownRecord
}

export interface ProductResearchTargetMarket {
  id: string
  platform: string
  site: string
  displayName: string
  searchMethods: ProductResearchMarketSearchMethodBinding[]
  raw: UnknownRecord
}

export interface ProductResearchConfig {
  searchProviders: ProductResearchSourceRegistryItem[]
  targetMarkets: ProductResearchTargetMarket[]
  sourceRegistry: ProductResearchSourceRegistryItem[]
  raw: UnknownRecord
}

export interface ProductResearchProviderTestResult {
  ok: boolean
  status: string
  sourceId: string
  providerStrategy: string
  market: string
  keyword: string
  itemsFound: number
  durationMs: number
  error: string
  sample: UnknownRecord
  raw: UnknownRecord
}

export interface AuthResult {
  ok: boolean
  message: string
  error: string
  errorCode: string
  nextAction: string
  raw: UnknownRecord
}

export interface MercadoLibreAuthChecklistField {
  key: string
  label: string
  ok: boolean
  value: string
}

export interface MercadoLibreAuthChecklist {
  platform: 'mercadolibre'
  readyForAuthLink: boolean
  tokenReady: boolean
  missingCodes: string[]
  fields: MercadoLibreAuthChecklistField[]
  nextAction: string
  copyText: string
  raw: UnknownRecord
}

export type MercadoLibreTestMode =
  | 'auth_link'
  | 'user_info'
  | 'refresh_token'
  | 'category_attrs'
  | 'image_upload'
  | 'payload_generate'
  | 'all'

export interface WorkflowStep {
  key: string
  title: string
  description: string
  status: 'pending' | 'active' | 'done' | 'blocked'
}
