import type { BackendConfirmedEdgeEstimate, BackendPublishPreparationProgress } from './workflow.generated'

// <schema-generated-types>
export {
  API_SCHEMA_VERSION,
  PRODUCT_SCHEMA_VERSION,
  type BackendUserAgentsDocument,
  type BackendDesktopCall,
  type BackendDesktopReply,
  type BackendAlibabaAuthorizationStatus,
  type BackendOneBoundShopBinding,
  type BackendOneBoundShopBindingPage,
  type BackendForbiddenCategory,
  type BackendForbiddenCategories,
  type BackendPublicEnterpriseApplication,
  type BackendPublicOneBoundApplication,
  type BackendPublicExchangeRateSettings,
  type BackendManagedSettings,
  type BackendTariffVersion,
  type BackendTariffSettings,
  type BackendTariffSettingsResponse,
  type BackendTariffRule,
  type BackendTariffRulesResponse,
  type BackendTariffFieldChange,
  type BackendTariffRuleChange,
  type BackendTariffDiff,
  type BackendWechatChannelStatus,
  type BackendOrderCenterFilters,
  type BackendOrderChatAttachment,
  type BackendOrderChatMessage,
  type BackendOrderChatHistory,
  type BackendOrderChatSent,
  type BackendAlibabaPurchaseAddressFields,
  type BackendAlibabaPurchaseAddressCandidate,
  type BackendAlibabaPurchaseAddresses,
  type BackendAlibabaParsedPurchaseAddress,
  type BackendAlibabaPaymentAgreement,
  type BackendAlibabaPurchasePayment,
  type BackendAlibabaSelfPurchaseCandidate,
  type BackendAlibabaBatchPurchaseItem,
  type BackendAlibabaSelfPurchasePreview,
  type BackendAlibabaSelfPurchaseRecord,
  type BackendAlibabaSelfPurchaseOptions,
  type BackendAlibabaBatchPurchaseLine,
  type BackendAlibabaBatchPurchaseOptions,
  type BackendAlibabaOrderStatus,
  type BackendAlibabaLogisticsStep,
  type BackendAlibabaParcel,
  type BackendAlibabaPurchaseProduct,
  type BackendAlibabaPurchaseQueryResult,
  type BackendPurchasePackageStatus,
  type BackendPurchaseTrackingSummary,
  type BackendPurchaseProgressView,
  type BackendOperationAvailability,
  type BackendDesktopHandoffCreated,
  type BackendDesktopHandoffStatus,
  type BackendDesktopHandoffResolved,
  type BackendDesktopInstaller,
  type BackendDesktopInstallers,
  type BackendApiResponse,
  type BackendAppStateResponse,
  type BackendDraftClaimTarget,
  type BackendCollectionVerification,
  type BackendCollectionVerificationStatus,
  type BackendCollectionLink,
  type BackendCollectionLinkEvent,
  type BackendAiCapabilityProfile,
  type BackendAiModelConfig,
  type BackendAiReasoningSettings,
  type BackendAiBrowserPreset,
  type BackendAiGenerationSettings,
  type BackendAiUseCaseBinding,
  type BackendWatermarkSettings,
  type BackendWatermarkPreview,
  type BackendSystemSettings,
  type BackendAppConfig,
  type BackendStoreConfig,
  type BackendSyncPolicyField,
  type BackendSyncPolicyItem,
  type BackendSyncPolicyCatalog,
  type BackendImageItem,
  type BackendDraftImageRef,
  type BackendImageBatchItemPayload,
  type BackendImageBatchSnapshot,
  type BackendImageSetPagePayload,
  type BackendImageSetPlanPayload,
  type BackendMercadoLibreMarketplaceBinding,
  type BackendMercadoLibreMarketplaceUser,
  type BackendMercadoLibreMarketPublication,
  type BackendMercadoLibrePublication,
  type BackendConfirmedEdgeEstimate,
  type BackendProductSku,
  type BackendProductSource,
  type BackendMercadoLibreSiteToSell,
  type BackendDraftTargetSite,
  type BackendPlatformDraft,
  type BackendProduct,
  type BackendProductResearchPrice,
  type BackendSorftimeQuotaReceipt,
  type BackendProductResearchKeywordRequest,
  type BackendProductResearchKeyword,
  type BackendProductResearchKeywordResponse,
  type BackendProductResearchSupplier,
  type BackendProductResearchSourcingQuery,
  type BackendProductResearchSourcing,
  type BackendProductResearchSupplierSearchResponse,
  type BackendProductResearchSupplierImportResponse,
  type BackendHotProductCandidate,
  type BackendProductResearchDataSource,
  type BackendProductResearchMarketSearchMethodBinding,
  type BackendProductResearchTargetMarket,
  type BackendProductResearchConfig,
  type BackendProductResearchSearchRequest,
  type BackendProductResearchSourceStatus,
  type BackendProductResearchRun,
  type BackendPublishPreparationCounts,
  type BackendPublishPreparationProgress,
  type BackendPublishConfirmation,
  type BackendPublishPlatformState,
  type BackendPublishJob,
  type BackendPublishJobSiteToSellSummary,
  type BackendPublishJobMarketResultSummary,
  type BackendPublishJobPlatformSummary,
  type BackendPublishJobSummary,
  type BackendTaskConfirmationSummary,
  type BackendQueueTask,
  type BackendTaskQueueResponse,
  type BackendTaskQueueDetail,
  type BackendOzonExportTemplate,
  type BackendOzonExportRow,
  type BackendOzonExportSummary,
  type BackendOzonExportPreview,
  type BackendOzonExportDownload,
} from './workflow.generated'
// </schema-generated-types>

export type Marketplace = string

export interface MarketplaceSiteOption {
  key: string
  code: string
  label: string
  language: string
}

export interface MarketplaceOption {
  key: Marketplace
  label: string
  titleLimit?: number
  sites: MarketplaceSiteOption[]
}

export interface CategoryAttributeDefinition {
  id: string
  name: string
  required: boolean
  variationRole?: string
  managedBy?: string
  options?: string[]
  valueType?: string
  valueMode?: 'strict_enum' | 'open_enum' | 'free_text' | string
  allowCustomValues?: boolean
  /** 本地 options 只是有界预览；true 时必须提供平台分页搜索入口。 */
  hasMoreValues?: boolean
  readOnly?: boolean
  /** 平台属性定义提供的文本格式及填写说明。 */
  textFormat?: '' | 'name_value_lines'
  formatHint?: string
  maxLength?: number
  minValue?: string
  maxValue?: string
  unit?: string
  /** 平台允许的计量单位列表（如 Yandex 带单位参数），空 = 无需选单位。 */
  unitOptions?: string[]
  /** 平台默认计量单位；unitOptions 非空时作为初始选中。 */
  defaultUnit?: string
  description?: string
  dictionaryId?: string
  isDictionary?: boolean
  isCollection?: boolean
  maxValueCount?: number
  categoryDependent?: boolean
}

export interface CategoryAttributeOption {
  id: string
  value: string
  info?: string
  picture?: string
}

export interface CategoryDictionaryValue {
  /** 平台枚举值 ID；自由文本集合项没有 ID，大 ID 平台按字符串保存避免精度丢失。 */
  dictionaryValueId?: string | number
  value: string
}

export interface CategoryDictionarySelection {
  values: CategoryDictionaryValue[]
}

export interface CategoryAttributeUnitValue {
  value: string
  unit: string
}

export type CategoryAttributeValue = string | CategoryDictionarySelection | CategoryAttributeUnitValue

/** /api/category-attribute-values 返回的单页枚举值；全集需按 nextCursor 翻页。 */
export interface CategoryAttributeValuesPage {
  values: CategoryAttributeOption[]
  nextCursor: string
  hasMore: boolean
  complete: boolean
}

/** 发布 payload 预览摘要（脱敏后由后端随预览返回）。 */
export interface PublishPayloadSummary {
  skuItems?: UnknownRecord[]
  groupingMode?: string
  productId: string
  draftId: string
  platform: string
  site: string
  storeIdentity: string
  storeLabel: string
  title: string
  categoryId: string
  listingCurrency: string
  price: string
  stock: string
  imageCount: number
}

export interface MarketplaceSiteToSell {
  siteId: string
  logisticType: string
  /** pricing_model=price 时使用的买家售价，与 netProceeds 互斥。 */
  price?: string
  listingTypeId?: string
  /** 仅允许由安全编辑界面写入 active / paused；留空表示不修改远端状态。 */
  status?: string
  freeShipping?: boolean
  saleTerms?: UnknownRecord[]
  /** pricing_model=net_proceeds 时使用，与 price 互斥。 */
  netProceeds?: string
}

/** 发布预览确认态：payload、digest 与摘要同生命周期，预览被清空即失效。 */
export type PublishPreparationState = Omit<BackendPublishPreparationProgress, 'stage'> & {
  stage: BackendPublishPreparationProgress['stage'] | 'saving_draft' | 'completed' | 'failed'
}

export interface PayloadPreviewState {
  platform: Marketplace
  site: string
  targetKey: string
  status: string
  path: string
  payload: UnknownRecord
  warning: string
  validationDigest: string
  summary: PublishPayloadSummary | null
  warnings: PrecheckIssue[]
}

export interface MarketplaceTargetSite {
  platform: Marketplace
  site: string
  language: string
  /** 核价时使用的店铺发布币种快照；事实源是店铺授权配置。 */
  listingCurrency: string
  /** 核价时的店铺币种配置指纹，用于发布前失效判断。 */
  currencyFingerprint?: string
  /** CBT Global Selling 刊登实际投放的子市场与物流模式。 */
  sitesToSell?: MarketplaceSiteToSell[]
  categoryId?: string
  descriptionCategoryId?: string
  categoryPath?: string
  attributes?: Record<string, CategoryAttributeValue>
  validationErrors?: Array<UnknownRecord | string>
  categoryPrecheck?: UnknownRecord
  publishStatus?: string
  status?: WorkflowStatus | string
  lastPrecheck?: UnknownRecord
  lastPrecheckTarget?: UnknownRecord
  lastPublishTask?: UnknownRecord
  publishLogs?: UnknownRecord[]
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

export type CollectMode = 'browser' | 'api'

export type UnknownRecord = Record<string, unknown>

export interface Money {
  amount: string
  currency: string
}

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
  storageKey?: string
  contentSha256?: string
  deliveryProvider?: string
  deliveryError?: string
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
  dimensions: {
    lengthCm: string
    widthCm: string
    heightCm: string
  }
  weightKg: string
  imagePool: ImageAsset[]
  attributes: UnknownRecord
  attributeMatches: UnknownRecord
  collectStatus: string
  collectDiagnostics: UnknownRecord
}

export interface ProductSku extends UnknownRecord {
  id: string
  source_sku_id: string
  source_offer_id?: string
  source_spec_id?: string
  name: string
  options: Record<string, string>
  cost_cny: string
  supplier_stock: string
  image_asset_id: string
  barcode: string
  package_dimensions: Record<string, string>
  package_estimates?: Record<string, BackendConfirmedEdgeEstimate>
  battery?: boolean
  liquid?: boolean
  active: boolean
  source_snapshot: UnknownRecord
}

export interface DraftSku extends UnknownRecord {
  pricing_overrides?: UnknownRecord
  sku_id: string
  selected: boolean
  sku: string
  stock: string
  overrides: UnknownRecord
  attributes_by_target: Record<string, Record<string, CategoryAttributeValue>>
  custom_attributes_by_target?: Record<string, { name: string; value: string }[]>
  source_option_translations?: Record<string, Record<string, string>>
  pricing: UnknownRecord
  publications: Record<string, UnknownRecord>
}

export interface MarketplaceDraft {
  skuItems: DraftSku[]
  grouping: { mode: string; name: string }
  draftId: string
  platforms: Marketplace[]
  targetSites: MarketplaceTargetSite[]
  site: string
  enabled: boolean
  /** Mercado traditional CBT 根刊登使用的英文标题。 */
  globalTitle: string
  title: string
  description: string
  categoryId: string
  descriptionCategoryId: string
  categoryPath: string
  attributes: Record<string, CategoryAttributeValue>
  pricing: UnknownRecord
  images: DraftImageRef[]
  status: WorkflowStatus
  language: string
  saleTerms: UnknownRecord[]
  guaranteePeriod?: { timePeriod?: number | string; timeUnit?: string; comment?: string }
  allowGtinExemption: boolean
  validationErrors: Array<UnknownRecord | string>
  publishStatus: string
  lastPrecheck: UnknownRecord
  lastPrecheckTarget: UnknownRecord
}

export interface Product {
  skuItems: ProductSku[]
  productId: string
  name: string
  brand: string
  model: string
  category: string
  sku: string
  stock: string
  upc: string
  materials: string[]
  packageIncludes: string[]
  attributes: UnknownRecord
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
  skuItems: ProductSku[]
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
  templateSource?: { id: string; name: string } | null
  battery?: boolean
  liquid?: boolean
  platform: Marketplace
  site: string
  purchaseCostCny: number
  domesticFreightCny: number
  packagingCostCny: number
  otherCostCny: number
  weightKg: number
  lengthCm: number
  widthCm: number
  heightCm: number
  usdCnyRate: number
  mxnUsdRate: number
  rubCnyRate: number
  exchangeRateMode: 'live' | 'manual'
  exchangeRateOverrides?: Partial<Record<'usd_cny_rate' | 'mxn_usd_rate' | 'rub_cny_rate', number>>
  selectedTargetKeys?: string[]
  targets: PricingTargetInput[]
}

export type PricingMode = 'margin' | 'markup' | 'manual'
export type ShippingQuoteMode = 'auto' | 'manual'
export type ShippingCurrency = 'USD' | 'CNY'

export interface PricingShippingSelection {
  warehouse_id: number
  route_id: string
}

export interface PricingTargetInput {
  returnRatePercent?: number | ''
  returnDisposition?: 'discard' | 'resell'
  returnHandlingCostCny?: number | ''
  categoryId?: string
  targetKey: string
  platform: Marketplace
  site: string
  /** CBT Global Selling 核价所绑定的销售子市场。 */
  sitesToSell: MarketplaceSiteToSell[]
  listingCurrency: string
  currencyFingerprint?: string
  paymentFeePercent: number
  otherFeePercent: number
  pricingMode: PricingMode
  targetMarginPercent: number
  markupPercent: number
  shippingQuoteMode: ShippingQuoteMode
  shippingCurrency: ShippingCurrency
  shippingAmount: number
  shippingSelection?: PricingShippingSelection | null
  manualPrice: Money | null
}

export type PricingDestinationModel = 'price' | 'net_proceeds'

/** Mercado Global Selling 单个销售 operation 的核价结果。 */
export interface PricingDestinationResult {
  shippingAmount?: number
  shippingCurrency?: string
  siteId: string
  logisticType: string
  pricingModel: PricingDestinationModel
  price: Money | null
  netProceeds: Money | null
  calculationFingerprint?: string
}

export interface PricingTargetResult {
  shippingCandidates?: UnknownRecord[]
  targetKey: string
  platform: Marketplace
  site: string
  listingCurrency: string
  currencyFingerprint?: string
  suggestedPrice: Money
  /** 买家看到的公开售价。 */
  appliedPrice: Money
  /** Mercado 扣除平台费用与国际物流后的期望到账额；不适用时为空。 */
  appliedNetProceeds: Money | null
  /** CBT 每个 site_id + logistic_type 对应的最终发布金额。 */
  destinationResults: PricingDestinationResult[]
  convertedPrices: Record<string, string>
  calculationBasis: UnknownRecord
  calculationFingerprint: string
  shippingCostUsd: number
  shippingCostCny: number
  totalCostCny: number
  netRevenueCny: number
  profitCny: number
  marginPercent: number
  commissionPercent: number
  paymentFeePercent: number
  otherFeePercent: number
  pricingMode: PricingMode
  targetMarginPercent: number
  markupPercent: number
  shippingQuoteMode: ShippingQuoteMode
  shippingCurrency: ShippingCurrency
  shippingAmount: number
  shippingSource: string
  commissionCny: number
  paymentFeeCny: number
  otherFeeCny: number
  minimumPrice: Money
  billableWeightKg: number
  usdCnyRate: number
  mxnUsdRate: number
  rubCnyRate: number
  isLoss: boolean
  errors: Array<UnknownRecord | string>
  raw: UnknownRecord
}

export interface PricingResult {
  previewId?: string
  results: PricingTargetResult[]
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
  requiredAttributes: CategoryAttributeDefinition[]
  optionalAttributes: CategoryAttributeDefinition[]
  source?: string
  /** 属性定义从实时类目接口加载完成的时间戳；仅恢复类目身份（未加载定义）时为空。 */
  fetchedAt?: string
  raw?: UnknownRecord
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

export interface CategoryMatchFailure {
  code: string
  message: string
  stage: string
  retryable: boolean
}

export interface CategoryMatchDecision {
  confidenceBand: 'high' | 'medium' | 'low'
  modelConfidence: number
  decisionScore: number
  abstained: boolean
  evidence: string[]
  searchCount: number
}

export interface CategoryMatchResult {
  ok: boolean
  status: 'completed' | 'unresolved' | 'failed'
  selectedCategoryId: string
  candidates: CategorySearchResult[]
  query: string
  decision: CategoryMatchDecision
  failure: CategoryMatchFailure | null
  trace: {
    taskRunId: string
  }
}

export interface CategoryPrecheckResult {
  ok: boolean
  errors: string[]
  missingFields: string[]
  checkedAt: string
  raw: UnknownRecord
}

export interface PrecheckRelatedIssue {
  code: string
  field: string
  message: string
  severity: 'error' | 'warning' | string
  nextAction: string
}

export interface PrecheckIssue extends PrecheckRelatedIssue {
  /** 合并问题的影响范围，仅保留身份，不复制逐 SKU 错误或商品资料。 */
  affectedSkuIds?: string[]
  affectedAssetIds?: string[]
  /** 已执行的关联校验，随主问题展示，不重复计数。 */
  relatedIssues?: PrecheckRelatedIssue[]
}

export type PublishPrecheckScopeStatus = 'blocked' | 'passed'

export interface PublishPrecheckScope {
  ok: boolean
  status: PublishPrecheckScopeStatus
  errors: PrecheckIssue[]
  warnings: PrecheckIssue[]
}

export interface PublishPrecheckMarketCheck extends PublishPrecheckScope {
  siteId: string
  logisticType: string
}

export type DraftPublishOperation = 'precheck' | 'preview' | 'publish'

export interface PublishPrecheck {
  ok: boolean
  errors: string[]
  warnings: string[]
  errorItems: PrecheckIssue[]
  warningItems: PrecheckIssue[]
  checkedAt: string
  /** Mercado Libre 分层预检；其他平台未返回 scope 时保持为空。 */
  parent?: PublishPrecheckScope | null
  marketChecks?: PublishPrecheckMarketCheck[]
}

export interface PublishJob {
  jobId: string
  status: 'queued' | 'running' | 'pending_confirmation' | 'completed' | 'failed' | 'outcome_unknown'
  platforms: Marketplace[]
  createdAt: string
  draftId?: string
  targetKey?: string
}

export interface CollectForm {
  productUrl: string
  productUrls: string
  platform: string
  mode: CollectMode
  outputDir: string
}

export interface CollectDiagnostics {
  status: 'idle' | 'running' | 'waiting_verification' | 'success' | 'failed' | 'unconfirmed'
  progress: number
  message: string
  downloadedImages: number
  antiBotWarning: boolean
  lastSourceUrl: string
  errorCode: string
  nextAction: string
  htmlSnapshotPath: string
  screenshotPath: string
  raw: UnknownRecord
}

export interface CollectionVerification {
  browserTabId: string
  sourceUrl: string
  platform: string
}

export interface CollectBatchResultRow {
  verification?: CollectionVerification
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

export type CollectBatchStatus = import('./workflow.generated').BackendCollectionLink['status']







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
  publishStatus: string
  createdAt: string
  updatedAt: string
  productFilePath: string
  raw: UnknownRecord
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
  rating: number | null
  reviewCount: number | null
  monthlySales?: number | null
  asin?: string
  importedProductId?: string
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
