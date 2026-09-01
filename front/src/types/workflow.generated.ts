/**
 * 此文件由 scripts/generate_frontend_types.py 自动生成。
 * 请修改 erp_web/schemas 后重新运行生成器，不要手工编辑。
 */

export const API_SCHEMA_VERSION = 1 as const
export const PRODUCT_SCHEMA_VERSION = 3 as const

export interface BackendApiResponse {
  schemaVersion?: number
  ok?: boolean
  error?: string
  error_code?: string
  message?: string
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

export interface BackendAppConfig {
  task_approval_mode?: "ask" | "full"
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
  id?: string
  url?: string
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
  storage_key?: string
  content_sha256?: string
  delivery_provider?: string
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

export interface BackendProductSource {
  source_platform?: string
  source_url?: string
  title?: string
  price?: string
  currency?: string
  description?: string
  bullets?: Array<string>
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
  stock?: string
  sku?: string
  upc?: string
  bullets?: Array<string>
  search_terms?: Array<string>
  language?: string
  package_dimensions?: Record<string, string>
  validation_errors?: Array<unknown>
  images?: Array<BackendDraftImageRef>
  sale_terms?: Array<Record<string, unknown>>
  allow_gtin_exemption?: boolean
  shipping?: Record<string, unknown>
  category_precheck?: Record<string, unknown>
  last_precheck?: Record<string, unknown>
  last_precheck_target?: Record<string, unknown>
  last_publish_task?: Record<string, unknown>
  publication?: BackendMercadoLibrePublication
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
  selling_points?: Array<string>
  package_includes?: Array<string>
  colors?: Array<string>
  avoid_claims?: Array<string>
  description?: string
  dimensions?: string
  weight_kg?: string
  source?: BackendProductSource
  drafts?: Record<string, BackendPlatformDraft>
  marketplace_terms?: Record<string, unknown>
  attributes?: Record<string, unknown>
  listing_overrides?: Record<string, unknown>
  copy_results?: Record<string, unknown>
  sku_items?: Array<Record<string, unknown>>
  selected_sku_indices?: Array<number>
  pricing_defaults?: Record<string, unknown>
  publish_preview?: Record<string, unknown>
  collect_status?: string
  collect_logs?: Array<unknown>
  workflow_statuses?: Record<string, string>
  created_at?: string
  updated_at?: string
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
