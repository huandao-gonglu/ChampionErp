export type Marketplace = 'mercadolibre' | 'wildberries' | 'ozon'

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

export type CollectMode = 'browser' | 'http' | 'manual' | 'extension'

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
  translatedFromId?: string
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
  enabled: boolean
  title: string
  description: string
  bullets: string[]
  categoryId: string
  categoryPath: string
  attributes: Record<string, string>
  price: string
  images: string[]
  status: WorkflowStatus
  language: string
  stock: string
  sku: string
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
  exchangeRateMode: 'live' | 'manual'
  displayCurrencyMode: 'platform' | 'cny'
}

export interface PricingResult {
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
  requiredAttributes: Array<{ id: string; name: string; required: boolean }>
}

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
