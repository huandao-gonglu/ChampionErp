/**
 * 此文件由 scripts/generate_frontend_types.py 自动生成。
 * 请修改 erp_web/schemas 后重新运行生成器，不要手工编辑。
 */

export const API_SCHEMA_VERSION = 1 as const
export const PRODUCT_SCHEMA_VERSION = 4 as const

export interface BackendAlibabaPurchaseAddressFields {
  fullName: string
  mobile: string
  phone: string
  provinceText: string
  cityText: string
  areaText: string
  townText: string
  address: string
  postCode: string
}

export interface BackendAlibabaPurchaseAddressCandidate {
  id: string
  kind: string
  label: string
  text: string
  is_default: boolean
  blocked_reason: string
}

export interface BackendAlibabaPurchaseAddresses {
  ok: boolean
  items: Array<BackendAlibabaPurchaseAddressCandidate>
  notice: string
}

export interface BackendAlibabaParsedPurchaseAddress {
  ok: boolean
  address: BackendAlibabaPurchaseAddressFields
  warnings: Array<string>
}

export interface BackendAlibabaSelfPurchaseCandidate {
  id: string
  offer_id: string
  sku_id: string
  specification: string
  product_url: string
}

export interface BackendAlibabaSelfPurchasePreview {
  candidate: BackendAlibabaSelfPurchaseCandidate
  quantity: number
  recipient: string
  address: string
  phone: string
  goods_fen: number
  shipping_fen: number
  total_fen: number
  flow: string
  pay_channels: Array<string>
  expires_at: number
}

export interface BackendAlibabaSelfPurchaseRecord {
  id: string
  state: string
  preview: BackendAlibabaSelfPurchasePreview
  order_numbers: Array<string>
  order_status: string
  message: string
  pay_channel: string
  created_at: string
  purchase_record_id: string
}

export interface BackendAlibabaSelfPurchaseOptions {
  ok: boolean
  remaining_quantity: number
  can_purchase: boolean
  blocked_reason: string
  candidates: Array<BackendAlibabaSelfPurchaseCandidate>
  records: Array<BackendAlibabaSelfPurchaseRecord>
}

export interface BackendAlibabaOrderStatus {
  order_number: string
  status: string
  status_label: string
}

export interface BackendAlibabaLogisticsStep {
  time: string
  description: string
}

export interface BackendAlibabaParcel {
  logistics_id: string
  company: string
  tracking_number: string
  status: string
  status_label: string
  steps: Array<BackendAlibabaLogisticsStep>
}

export interface BackendAlibabaPurchaseQueryResult {
  ok: boolean
  record_id: string
  order_number: string
  checked_at: string
  order: BackendAlibabaOrderStatus | null
  logistics: Array<BackendAlibabaParcel> | null
  logistics_warning: string
  logistics_checked_at?: string
}

export interface BackendPurchaseTrackingSummary {
  orders: Array<BackendAlibabaOrderStatus>
  unknown_count: number
  has_waybill: boolean
  stale: boolean
}

export interface BackendPurchaseProgressView {
  state: "syncing" | "synced" | "pending_assignment" | "conflict" | "locked" | "manual" | "error"
  attempted_at: string
  message: string
  error: string
  data: BackendAlibabaPurchaseQueryResult | null
}

export interface BackendApiResponse {
  schemaVersion?: number
  ok?: boolean
  error?: string
  error_code?: string
  message?: string
  status?: string
  verification?: BackendCollectionVerification
  data?: unknown
  items?: Array<unknown>
  product?: Record<string, unknown>
  productsIndex?: Array<Record<string, unknown>>
  draftsIndex?: Array<Record<string, unknown>>
  task?: Record<string, unknown>
}

export interface BackendAppStateResponse {
  schemaVersion: number
  ok: boolean
  product: BackendProduct
  appConfig: Record<string, unknown>
  storeConfig: Record<string, unknown>
  storeAuthSummary: Record<string, unknown>
  mercadolibreAuthChecklist: Record<string, unknown>
  imagePool: Array<Record<string, unknown>>
  sourceImages: Array<string>
  generatedImages: Array<Record<string, unknown>>
  platformOptions: Array<Record<string, unknown>>
  outputDir: string
  approvalToken: string
}

export interface BackendDraftClaimTarget {
  platform: string
  site: string
  language: string
}

export interface BackendCollectionVerification {
  browser_tab_id: string
  source_url: string
  platform: string
}

export interface BackendCollectionVerificationStatus {
  ok: boolean
  status: "waiting_verification" | "loading" | "ready" | "unavailable"
  message: string
}

export interface BackendAiCapabilityProfile {
  version?: number
  tested?: boolean
  connection_type?: string
  provider_id?: string
  api_style?: string
  model?: string
  base_url?: string
  request_mode?: string
  operation?: string
  strategy?: string
  tested_at?: string
  probe_version?: string
  configuration_fingerprint?: string
}

export interface BackendAiModelConfig {
  id?: string
  name?: string
  connection_type?: string
  provider_id?: string
  provider?: string
  api_style?: string
  base_url?: string
  model?: string
  capabilities?: Array<string>
  capability_profiles?: Record<string, BackendAiCapabilityProfile>
  timeout_seconds?: number
  thinking_enabled?: boolean
  extra?: Record<string, unknown>
  enabled?: boolean
}

export interface BackendAiReasoningSettings {
  mode?: string
  effort?: string
  budget_tokens?: number
}

export interface BackendAiGenerationSettings {
  temperature?: number
  max_output_tokens?: number
  reasoning?: BackendAiReasoningSettings
}

export interface BackendAiUseCaseBinding {
  model_id?: string
  timeout_override_seconds?: number
  generation?: BackendAiGenerationSettings
}

export interface BackendSystemSettings {
  orders_auto_sync_interval_hours: number
}

export interface BackendAppConfig {
  system_settings?: BackendSystemSettings
  image_hosting?: Record<string, unknown>
  ai_tool_approval_mode?: "ask" | "full"
  ai_models?: Array<BackendAiModelConfig>
  ai_use_case_bindings?: Record<string, BackendAiUseCaseBinding>
  ai_use_case_prompts?: Record<string, Record<string, string>>
  pricing?: Record<string, unknown>
  pricing_defaults?: Record<string, unknown>
  product_research?: Record<string, unknown>
  browser?: Record<string, unknown>
}

export interface BackendStoreConfig {
  mercadolibre?: Record<string, unknown>
  yandex?: Record<string, unknown>
  ozon?: Record<string, unknown>
}

export interface BackendImageItem {
  hosting_profile_id?: string
  delivery_fingerprint?: string
  delivery_provider?: string
  storage_key?: string
  content_sha256?: string
  url?: string
  id?: string
  path?: string
  preview_url?: string
  width?: number
  height?: number
  size_label?: string
  origin?: string
  usage?: string
  platforms?: Array<string>
  selected?: boolean
  is_main?: boolean
  is_sku?: boolean
  order?: number
  status?: string
  sku?: string
  note?: string
  derived_from_id?: string
  source_asset_id?: string
  target_language?: string
  provider?: string
  translate_job_id?: string
  delivery_error?: string
  platform_picture_id?: string
  mercadolibre_picture_id?: string
  upload_status?: string
  upload_error?: string
  uploaded_at?: string
  platform_uploads?: Record<string, Record<string, unknown>>
  raw?: Record<string, unknown>
}

export interface BackendDraftImageRef {
  asset_id?: string
  role?: string
  order?: number
  label?: string
  note?: string
  alt_text?: string
  source_asset_id?: string
}

export interface BackendMercadoLibreMarketplaceBinding {
  seller_id: string
  site_id: string
  logistic_type: string
  business_model: string
  pricing_model: string
  user_product: boolean | null
}

export interface BackendMercadoLibreMarketplaceUser {
  user_id: string
  site_id: string
  marketplace_bindings: Array<BackendMercadoLibreMarketplaceBinding>
}

export interface BackendMercadoLibreMarketPublication {
  site_id?: string
  seller_id?: string
  logistic_type?: string
  item_id?: string
  user_product_id?: string
  status?: string
  price?: number | string
  net_proceeds?: number | string
  free_shipping?: boolean
  sale_terms?: Array<Record<string, unknown>>
  currency_id?: string
  listing_type_id?: string
  error?: Record<string, unknown> | Array<unknown> | string
  last_operation?: Record<string, unknown>
  updated_at?: string
}

export interface BackendMercadoLibrePublication {
  model?: string
  account_user_id?: string
  parent_item_id?: string
  parent_user_product_id?: string
  siteless_user_product_id?: string
  siteless_family_id?: string
  seller_id?: string
  family_name?: string
  status?: string
  markets?: Array<BackendMercadoLibreMarketPublication>
  confirmed_payload?: Record<string, unknown>
  error?: Record<string, unknown> | Array<unknown> | string
  last_operation?: Record<string, unknown>
  updated_at?: string
}

export interface BackendProductSku {
  id?: string
  source_sku_id?: string
  source_offer_id?: string
  source_spec_id?: string
  name?: string
  options?: Record<string, string>
  cost_cny?: string
  supplier_stock?: string
  image_asset_id?: string
  barcode?: string
  package_dimensions?: Record<string, string>
  active?: boolean
  source_snapshot?: Record<string, unknown>
}

export interface BackendProductSource {
  source_platform?: string
  source_url?: string
  title?: string
  price?: string
  currency?: string
  material?: string
  package_contents?: Array<string>
  variants?: Array<Record<string, unknown>>
  skus?: Array<Record<string, unknown>>
  attributes?: Record<string, unknown>
  attribute_matches?: Record<string, unknown>
  dimensions?: Record<string, string>
  weight_kg?: string
  images?: Array<string>
  image_pool?: Array<BackendImageItem>
  collect_status?: string
  collect_logs?: Array<unknown>
  collect_diagnostics?: Record<string, unknown>
  brand?: string
  model?: string
  sku?: string
  created_at?: string
}

export interface BackendMercadoLibreSiteToSell {
  site_id?: string
  logistic_type?: string
  price?: number | string
  net_proceeds?: number | string
  listing_type_id?: string
  status?: string
  free_shipping?: boolean
  sale_terms?: Array<Record<string, unknown>>
}

export interface BackendDraftTargetSite {
  platform?: string
  site?: string
  language?: string
  listing_currency?: string
  currency_fingerprint?: string
  category_id?: string
  description_category_id?: string
  category_path?: string
  attributes?: Record<string, unknown>
  validation_errors?: Array<unknown>
  category_precheck?: Record<string, unknown>
  publish_status?: string
  status?: string
  last_precheck?: Record<string, unknown>
  last_precheck_target?: Record<string, unknown>
  last_publish_task?: Record<string, unknown>
  sites_to_sell?: Array<BackendMercadoLibreSiteToSell>
}

export interface BackendPlatformDraft {
  sku_items?: Array<Record<string, unknown>>
  grouping?: Record<string, unknown>
  draft_id?: string
  product_id?: string
  source_product_id?: string
  platform?: string
  platforms?: Array<string>
  enabled?: boolean
  site?: string
  country?: string
  status?: string
  publish_status?: string
  global_title?: string
  title?: string
  description?: string
  brand?: string
  model?: string
  category_id?: string
  description_category_id?: string
  category_path?: string
  target_sites?: Array<BackendDraftTargetSite>
  attributes?: Record<string, unknown>
  pricing?: Record<string, unknown>
  search_terms?: Array<string>
  language?: string
  validation_errors?: Array<unknown>
  images?: Array<BackendDraftImageRef>
  sale_terms?: Array<Record<string, unknown>>
  allow_gtin_exemption?: boolean
  shipping?: Record<string, unknown>
  category_precheck?: Record<string, unknown>
  last_precheck?: Record<string, unknown>
  last_precheck_target?: Record<string, unknown>
  last_publish_task?: Record<string, unknown>
  ai_copy_ready?: boolean
  copy_generated_at?: string
  copy_source?: string
  copy_operation_key?: string
  created_at?: string
  updated_at?: string
}

export interface BackendProduct {
  schema_version?: number
  product_id?: string
  name?: string
  brand?: string
  model?: string
  category?: string
  target_customer?: string
  sku?: string
  stock?: string
  upc?: string
  cost?: string
  materials?: Array<string>
  package_includes?: Array<string>
  colors?: Array<string>
  avoid_claims?: Array<string>
  dimensions?: string
  weight_kg?: string
  source?: BackendProductSource
  drafts?: Record<string, BackendPlatformDraft>
  marketplace_terms?: Record<string, unknown>
  attributes?: Record<string, unknown>
  listing_overrides?: Record<string, unknown>
  copy_results?: Record<string, unknown>
  sku_items?: Array<BackendProductSku>
  pricing_defaults?: Record<string, unknown>
  publish_preview?: Record<string, unknown>
  collect_status?: string
  collect_logs?: Array<unknown>
  workflow_statuses?: Record<string, string>
  created_at?: string
  updated_at?: string
}

export interface BackendProductResearchPrice {
  amount?: number
  currency?: string
}

export interface BackendSorftimeQuotaReceipt {
  endpoint: string
  domain: number
  request_consumed: number | null
  request_left: number | null
}

export interface BackendProductResearchSupplier {
  product_id: string
  title: string
  source_url: string
  image_url: string
  price_cny: number | null
  store_name: string
  service_score: number | null
  monthly_sales: number | null
  min_order_quantity: number | null
  repurchase_rate: number | null
  collected_at: string
}

export interface BackendProductResearchSourcingQuery {
  mode: "keyword" | "image"
  keyword: string
}

export interface BackendProductResearchSourcing {
  query?: BackendProductResearchSourcingQuery
  status?: string
  items?: Array<BackendProductResearchSupplier>
  quota_receipts?: Array<BackendSorftimeQuotaReceipt>
}

export interface BackendProductResearchSupplierSearchResponse {
  ok: boolean
  sourcing: BackendProductResearchSourcing
  cached: boolean
}

export interface BackendProductResearchSupplierImportResponse {
  ok: boolean
  product_id: string
  already_imported: boolean
  quota_receipts: Array<BackendSorftimeQuotaReceipt>
}

export interface BackendHotProductCandidate {
  id?: string
  title?: string
  image_url?: string
  rank?: number
  source_url?: string
  market_id?: string
  platform?: string
  site?: string
  keyword?: string
  price?: BackendProductResearchPrice
  rating?: number | null
  review_count?: number | null
  hot_score?: number
  source_name?: string
  collected_at?: string
  asin?: string
  monthly_sales?: number | null
  data_updated_at?: string
  sourcing?: BackendProductResearchSourcing
  imported_product_id?: string
  import_quota_receipts?: Array<BackendSorftimeQuotaReceipt>
}

export interface BackendProductResearchDataSource {
  id?: string
  name?: string
  source_type?: "api" | "ai_search" | "crawler" | "third_party_api" | "manual_import"
  platform?: string
  enabled?: boolean
  priority?: number
  supported_markets?: Array<string>
  supported_languages?: Array<string>
  supported_data_types?: Array<string>
  auth_required?: boolean
  rate_limit_per_minute?: number | null
  compliance_note?: string
  config_json?: Record<string, unknown>
}

export interface BackendProductResearchMarketSearchMethodBinding {
  method_id?: string
  enabled?: boolean
  prompt?: string
  config_json?: Record<string, unknown>
}

export interface BackendProductResearchTargetMarket {
  id?: string
  platform?: string
  site?: string
  display_name?: string
  search_methods?: Array<BackendProductResearchMarketSearchMethodBinding>
}

export interface BackendProductResearchConfig {
  search_defaults?: Record<string, unknown>
  provider_runtime?: Record<string, unknown>
  search_providers?: Array<BackendProductResearchDataSource>
  target_markets?: Array<BackendProductResearchTargetMarket>
  source_registry?: Array<BackendProductResearchDataSource>
}

export interface BackendProductResearchSearchRequest {
  search_mode?: "target_only" | "target_plus_reference" | "global_scan"
  markets?: Record<string, Array<string>>
  keywords?: Array<string>
  result_options?: Record<string, unknown>
}

export interface BackendProductResearchSourceStatus {
  source?: string
  source_id?: string
  market?: string
  status?: string
  items_found?: number
  error_message?: string
  provider_strategy?: string
  raw_items_found?: number
  items_filtered?: number
  diagnostic_message?: string
  quota_receipts?: Array<BackendSorftimeQuotaReceipt>
  ai_model_id?: string
  api_style?: string
  stream_enabled?: boolean
  stream_fallback_used?: boolean
}

export interface BackendProductResearchRun {
  run_id?: string
  status?: string
  search_mode?: string
  created_at?: string
  completed_at?: string
  description?: string
  progress_description?: string
  request?: BackendProductResearchSearchRequest
  items?: Array<BackendHotProductCandidate>
  source_status?: Array<BackendProductResearchSourceStatus>
}

export interface BackendPublishConfirmation {
  submitted_at?: string
  next_check_at?: string
  last_checked_at?: string
  check_error?: string
}

export interface BackendPublishPlatformState {
  platform?: string
  product_id?: string
  draft_id?: string
  site?: string
  status?: string
  stage?: string
  error?: string
  result?: Record<string, unknown> | null
  attempts?: number
  created_at?: string
  updated_at?: string
  category_id?: string
  confirmation?: BackendPublishConfirmation
}

export interface BackendPublishJob {
  job_id?: string
  draft_id?: string
  status?: string
  product_name?: string
  product?: BackendProduct
  platforms?: Record<string, BackendPublishPlatformState>
  persisted_drafts?: Record<string, Record<string, unknown>>
  created_at?: string
  updated_at?: string
}

export interface BackendPublishJobSiteToSellSummary {
  site_id: string
  logistic_type: string
}

export interface BackendPublishJobMarketResultSummary {
  site_id: string
  logistic_type: string
  status: string
  item_id: string
  error: string
  error_code: string
}

export interface BackendPublishJobPlatformSummary {
  platform: string
  draft_id: string
  site: string
  sites_to_sell: Array<BackendPublishJobSiteToSellSummary>
  market_results: Array<BackendPublishJobMarketResultSummary>
  status: string
  stage: string
  attempts: number
  error: string
  error_code: string
  next_action: string
  updated_at: string
  confirmation: BackendPublishConfirmation
}

export interface BackendPublishJobSummary {
  job_id: string
  product_id: string
  product_name: string
  draft_id: string
  status: string
  raw_status: string
  stage: string
  attempts: number
  error: string
  error_code: string
  next_action: string
  platforms: Array<BackendPublishJobPlatformSummary>
  created_at: string
  updated_at: string
}

export interface BackendOzonExportTemplate {
  category: string
  category_id: string
  currency: string
  required_fields: Array<string>
}

export interface BackendOzonExportRow {
  listing_id: string
  seller_sku: string
  version: string
  title_before: string
  title_changed: boolean
  fields: Record<string, string>
  errors: Array<string>
  warnings: Array<string>
}

export interface BackendOzonExportSummary {
  total: number
  ready: number
  missing: number
  barcode_missing: number
  title_changed: number
}

export interface BackendOzonExportPreview {
  ok: boolean
  template: BackendOzonExportTemplate
  rows: Array<BackendOzonExportRow>
  summary: BackendOzonExportSummary
  preview_fingerprint: string
}

export interface BackendOzonExportDownload {
  ok: boolean
  filename: string
  file_base64: string
}
