/**
 * 此文件由 scripts/generate_frontend_types.py 自动生成。
 * 请修改 erp_web/schemas 后重新运行生成器，不要手工编辑。
 */

export const API_SCHEMA_VERSION = 1 as const
export const PRODUCT_SCHEMA_VERSION = 5 as const

export interface BackendUserAgentsDocument {
  content: string
  revision: string
}

export interface BackendDesktopCall {
  id: string
  operation: "browser.status" | "collect.read" | "collect.verify" | "browser.ai.open" | "browser.ai.chat" | "canvas.check" | "canvas.edit" | "cli.run" | "cli.status" | "config.read" | "config.write" | "auth.open"
  params: Record<string, unknown>
  timeout: number
}

export interface BackendDesktopReply {
  id: string
  ok: boolean
  result: Record<string, unknown>
  error: string
  error_code: string
  outcome_unknown: boolean
}

export interface BackendAlibabaAuthorizationStatus {
  ok: boolean
  application_type: "alibaba" | "alibaba_crossborder"
  application_configured: boolean
  can_authorize: boolean
  authorized: boolean
  member_id: string
  account_name: string
  attempt: "idle" | "waiting" | "expired" | "failed"
  expires_at: number
  refreshable: boolean
  needs_reauthorization: boolean
}

export interface BackendForbiddenCategory {
  category_id: string
  name: string
}

export interface BackendForbiddenCategories {
  platform: "yandex"
  filename: string
  imported_at: string
  categories: Array<BackendForbiddenCategory>
}

export interface BackendPublicEnterpriseApplication {
  app_key: string
  redirect_uri: string
  secret_configured: boolean
}

export interface BackendPublicExchangeRateSettings {
  exchange_rate_api_url: string
  exchange_rate_timeout_seconds: string
  exchange_rate_cache_ttl_seconds: string
}

export interface BackendManagedSettings {
  ok: boolean
  revision: string
  exchange_rates: BackendPublicExchangeRateSettings
  alibaba: BackendPublicEnterpriseApplication
  alibaba_crossborder: BackendPublicEnterpriseApplication
  alibaba_api_application: "alibaba" | "alibaba_crossborder"
  crossborderbus_secret_configured: boolean
}

export interface BackendTariffVersion {
  version: string
  effective_date: string
  rules_count: number
  error: string
}

export interface BackendTariffSettings {
  version: string
  effective_date: string
  rules_count: number
  error: string
  platform: "yandex" | "ozon"
  source: "personal" | "shared"
  shared: BackendTariffVersion
  update_available: boolean
}

export interface BackendTariffSettingsResponse {
  ok: boolean
  items: Array<BackendTariffSettings>
}

export interface BackendTariffRule {
  id: string
  name: string
  carrier: string
  service: string
  transport: string
  group: string
  currency: string
  fixed: string
  per_g: string
  step_g: string
  min_g: string
  max_g: string
  price_min: string
  price_max: string
  price_currency: string
  max_edge_cm: string
  max_sum_cm: string
  volume_divisor_g: string
  battery: boolean | null
  liquid: boolean | null
  source_cell: string
}

export interface BackendTariffRulesResponse {
  ok: boolean
  platform: "yandex" | "ozon"
  source: "personal" | "shared"
  version: string
  filename: string
  sheet: string
  imported_at: string
  effective_date: string
  warnings: Array<string>
  calculation_notes: Array<string>
  rules: Array<BackendTariffRule>
}

export interface BackendTariffFieldChange {
  before: unknown
  after: unknown
}

export interface BackendTariffRuleChange {
  id: string
  name: string
  fields: Record<string, BackendTariffFieldChange>
}

export interface BackendTariffDiff {
  after_version: string
  added: Array<Record<string, unknown>>
  removed: Array<Record<string, unknown>>
  changed: Array<BackendTariffRuleChange>
  policy_change: unknown
  status: string
}

export interface BackendWechatChannelStatus {
  ok: boolean
  bound: boolean
  connected: boolean
  status: string
  conversation_id: string
  qr_image: string
  error: string
  pending_deliveries: number
  forward_types: Array<"reply" | "progress" | "approval" | "error" | "command">
}

export interface BackendOrderCenterFilters {
  shop?: string
  procurement_status?: "" | "unpurchased" | "partial" | "purchased" | "unknown"
  declaration_status?: "" | "unsubmitted" | "submitting" | "unknown" | "created" | "error" | "cancelled"
  warehouse_status?: "" | "NEW" | "FULFILLMENT_CREATED" | "WAITING_DOMESTIC_SHIPMENT" | "WAREHOUSE_RECEIVED" | "PACKING" | "SHIPPED" | "COMPLETED" | "EXCEPTION" | "CANCELLED"
  has_waybill?: "" | "yes" | "no"
  domestic_status?: "" | "waiting" | "in_transit" | "signed" | "unsigned" | "unknown"
  domestic_carrier?: string
  receiving_warehouse?: string
  platform_carrier?: string
  exception_type?: "" | "any" | "purchase" | "label" | "declaration" | "warehouse"
  deadline_from?: string
  deadline_to?: string
  deadline_status?: "" | "today" | "tomorrow" | "overdue"
  time_zone?: string
}

export interface BackendOrderChatAttachment {
  name: string
  url: string
}

export interface BackendOrderChatMessage {
  id: string
  sender: string
  created_at: string
  text: string
  attachments: Array<BackendOrderChatAttachment>
}

export interface BackendOrderChatHistory {
  ok: boolean
  chat_id: string
  messages: Array<BackendOrderChatMessage>
}

export interface BackendOrderChatSent {
  ok: boolean
  chat_id: string
}

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

export interface BackendAlibabaPaymentAgreement {
  channel: "alipay" | "shegou"
  ready: boolean
  signed: boolean
  bound: boolean
  sign_url: string
  message: string
}

export interface BackendAlibabaPurchasePayment {
  state: string
  channel: string
  amount_fen: number
  request_id: string
  message: string
}

export interface BackendAlibabaSelfPurchaseCandidate {
  id: string
  offer_id: string
  sku_id: string
  specification: string
  product_url: string
}

export interface BackendAlibabaBatchPurchaseItem {
  line_key: string
  title: string
  supplier: string
  candidate: BackendAlibabaSelfPurchaseCandidate
  quantity: number
}

export interface BackendAlibabaSelfPurchasePreview {
  items?: Array<BackendAlibabaBatchPurchaseItem>
  supplier?: string
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
  payment?: BackendAlibabaPurchasePayment | null
  purchase_record_ids?: Array<string>
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
  warehouse_required: boolean
  receiving_revision: number
  warehouse_name: string
  receiving_address: BackendAlibabaPurchaseAddressFields | null
  ok: boolean
  remaining_quantity: number
  can_purchase: boolean
  blocked_reason: string
  candidates: Array<BackendAlibabaSelfPurchaseCandidate>
  records: Array<BackendAlibabaSelfPurchaseRecord>
}

export interface BackendAlibabaBatchPurchaseLine {
  line_key: string
  title: string
  sku: string
  supplier: string
  options: BackendAlibabaSelfPurchaseOptions
}

export interface BackendAlibabaBatchPurchaseOptions {
  ok: boolean
  lines: Array<BackendAlibabaBatchPurchaseLine>
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

export interface BackendAlibabaPurchaseProduct {
  item_id: string
  quantity: number | null
  offer_id: string
  sku_id: string
  spec_id: string
  name: string
  specification: string
  image_url: string
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
  products: Array<BackendAlibabaPurchaseProduct>
}

export interface BackendPurchasePackageStatus {
  id: string
  order_numbers: Array<string>
  company: string
  tracking_number: string
  source: "order" | "logistics"
  status: string
  status_label: string
  updated_at: string
  stale: boolean
}

export interface BackendPurchaseTrackingSummary {
  packages?: Array<BackendPurchasePackageStatus>
  orders: Array<BackendAlibabaOrderStatus>
  unknown_count: number
  has_waybill: boolean
  stale: boolean
}

export interface BackendPurchaseProgressView {
  state: "syncing" | "synced" | "pending_review" | "locked" | "error"
  attempted_at: string
  message: string
  error: string
  data: BackendAlibabaPurchaseQueryResult | null
}

export interface BackendOperationAvailability {
  desktop_reason: string
  blocked_use_cases: Record<string, string>
  desktop_use_cases: Array<string>
}

export interface BackendDesktopHandoffCreated {
  ok: boolean
  request_id: string
  expires_in: number
}

export interface BackendDesktopHandoffStatus {
  ok: boolean
  opened: boolean
}

export interface BackendDesktopHandoffResolved {
  ok: boolean
  target: string
  state: Record<string, unknown>
  opened: boolean
}

export interface BackendDesktopInstaller {
  label: string
  version: string
  url: string
}

export interface BackendDesktopInstallers {
  ok: boolean
  items: Array<BackendDesktopInstaller>
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
  local_connection_id?: string
  local_config_fingerprint?: string
  browser_provider?: string
  browser_mode?: string
  browser_profile?: string
  browser_port?: string
  browser_url?: string
  browser_proxy?: string
  extra?: Record<string, unknown>
  enabled?: boolean
}

export interface BackendAiReasoningSettings {
  mode?: string
  effort?: string
  budget_tokens?: number
}

export interface BackendAiBrowserPreset {
  id: string
  label: string
  description: string
  model: BackendAiModelConfig
  supported_capabilities: Array<string>
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

export interface BackendWatermarkSettings {
  text: string
  image_data_url: string
  opacity: number
  size_percent: number
  density: number
  color: string
}

export interface BackendWatermarkPreview {
  preview_url: string
  sample_name: string
  message: string
}

export interface BackendSystemSettings {
  orders_auto_sync_interval_hours: number
  watermark: BackendWatermarkSettings
  sync_policies: Record<string, Record<string, number>>
  wechat_forward_types: Array<"reply" | "progress" | "approval" | "error" | "command">
}

export interface BackendAppConfig {
  system_settings?: BackendSystemSettings
  forbidden_categories?: BackendForbiddenCategories
  image_hosting?: Record<string, unknown>
  ai_tool_approval_mode?: "ask" | "full"
  ai_models?: Array<BackendAiModelConfig>
  ai_use_case_bindings?: Record<string, BackendAiUseCaseBinding>
  pricing?: Record<string, unknown>
  pricing_defaults?: Record<string, unknown>
  yandex_warehouses?: Record<string, unknown>
  pricing_templates?: Record<string, unknown>
  product_research?: Record<string, unknown>
  browser?: Record<string, unknown>
}

export interface BackendStoreConfig {
  mercadolibre?: Record<string, unknown>
  yandex?: Record<string, unknown>
  ozon?: Record<string, unknown>
}

export interface BackendSyncPolicyField {
  key: string
  label: string
  default: number
  minimum: number
  maximum: number
  step: number
  unit: string
}

export interface BackendSyncPolicyItem {
  key: string
  title: string
  description: string
  effect: string
  editable: boolean
  fields: Array<BackendSyncPolicyField>
  values: Record<string, number>
}

export interface BackendSyncPolicyCatalog {
  items: Array<BackendSyncPolicyItem>
}

export interface BackendImageItem {
  hosting_profile_id?: string
  delivery_fingerprint?: string
  delivery_provider?: string
  storage_key?: string
  content_sha256?: string
  watermark_fingerprint?: string
  delivery_source_sha256?: string
  watermark_source_url?: string
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

export interface BackendImageBatchItemPayload {
  index: number
  source_id: string
  source_url: string
  status: "queued" | "running" | "success" | "failed"
  result_urls: Array<string>
  error: string
  outcome_unknown: boolean
  started_at: string
  finished_at: string
  result_asset_ids?: Array<string>
}

export interface BackendImageBatchSnapshot {
  conversation_id: string
  product_id: string
  draft_id: string
  prompt: string
  items: Array<BackendImageBatchItemPayload>
  created_at: string
  updated_at: string
  queued: number
  running: number
  success: number
  failed: number
  total: number
  completed: number
  terminal: boolean
  image_set?: BackendImageSetPlanPayload | null
}

export interface BackendImageSetPagePayload {
  key: string
  title: string
  role: "main" | "detail" | "scene" | "selling_point" | "material" | "size" | "package"
  prompt: string
  source_image_ids: Array<string>
  scope?: "common" | "sku"
  sku_ids?: Array<string>
}

export interface BackendImageSetPlanPayload {
  style: string
  pages: Array<BackendImageSetPagePayload>
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

export interface BackendConfirmedEdgeEstimate {
  low: number
  suggested: number
  high: number
  basis: string
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
  package_estimates?: Record<string, BackendConfirmedEdgeEstimate>
  battery?: boolean
  liquid?: boolean
  active?: boolean
  source_snapshot?: Record<string, unknown>
}

export interface BackendProductSource {
  source_platform?: string
  source_url?: string
  title?: string
  price?: string
  legacy_product_cost_cny?: string
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
  images_manually_modified_at?: string
  image_preparation?: Record<string, string>
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
  guarantee_period?: Record<string, unknown>
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

export interface BackendProductResearchKeywordRequest {
  market_id: string
  keyword: string
  refresh: boolean
}

export interface BackendProductResearchKeyword {
  keyword: string
  chinese_name: string
  search_volume: number | null
  rank: number | null
  rank_change: number | null
  data_updated_at: string
}

export interface BackendProductResearchKeywordResponse {
  ok: boolean
  error: string
  market_id: string
  keyword: string
  items: Array<BackendProductResearchKeyword>
  cached: boolean
  fetched_at: string
  expires_at: string
  quota_receipts: Array<BackendSorftimeQuotaReceipt>
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

export interface BackendPublishPreparationCounts {
  completed?: number
  total?: number
}

export interface BackendPublishPreparationProgress {
  completed?: number
  total?: number
  stage: "validating_source" | "uploading" | "saving_images" | "validating_final" | "building_preview" | "saving_preview"
}

export interface BackendPublishConfirmation {
  submitted_at?: string
  next_check_at?: string
  last_checked_at?: string
  check_error?: string
  requires_attention?: boolean
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

export interface BackendTaskConfirmationSummary {
  state: string
  checks_used: number
  checks_remaining: number
  requests_used: number
  requests_remaining: number
  last_checked_at: number
  next_check_at: number
  deadline_at: number
  error: string
}

export interface BackendQueueTask {
  id: string
  job_id: string
  source: "publish" | "online"
  origin: string
  platform: string
  account_id: string
  operation: string
  title: string
  target_id: string
  status: string
  business_status: string
  created_at: string
  updated_at: string
  error: string
  protection: string
  confirmation: BackendTaskConfirmationSummary | null
  can_retry: boolean
}

export interface BackendTaskQueueResponse {
  ok: boolean
  items: Array<BackendQueueTask>
  total: number
  page: number
  accounts: Array<string>
}

export interface BackendTaskQueueDetail {
  ok: boolean
  task: BackendQueueTask
  detail: Record<string, unknown>
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
