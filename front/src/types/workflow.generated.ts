/**
 * 此文件由 scripts/generate_frontend_types.py 自动生成。
 * 请修改 erp_web/schemas 后重新运行生成器，不要手工编辑。
 */

export const API_SCHEMA_VERSION = 1 as const
export const PRODUCT_SCHEMA_VERSION = 2 as const

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

export interface BackendCategoryAttributeDefinition {
  id?: string
  name?: string
  required?: boolean
  options?: Array<string>
  value_type?: string
  unit?: string
  description?: string
  dictionary_id?: string
  is_dictionary?: boolean
  is_collection?: boolean
  max_value_count?: number
  category_dependent?: boolean
  raw?: Record<string, unknown>
}

export interface BackendCategoryAttributeSchema {
  version?: number
  platform?: string
  site?: string
  category_id?: string
  category_path?: string
  source?: string
  fetched_at?: string
  required?: Array<BackendCategoryAttributeDefinition>
  optional?: Array<BackendCategoryAttributeDefinition>
}

export interface BackendDraftTargetSite {
  platform?: string
  site?: string
  language?: string
  market_currency?: string
  listing_currency?: string
  currency_resolution?: Record<string, unknown>
  category_id?: string
  description_category_id?: string
  category_path?: string
  category_attribute_schema?: BackendCategoryAttributeSchema
  attributes?: Record<string, unknown>
  validation_errors?: Array<unknown>
  category_precheck?: Record<string, unknown>
  publish_status?: string
  status?: string
  last_precheck?: Record<string, unknown>
  last_precheck_target?: Record<string, unknown>
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
  title?: string
  description?: string
  brand?: string
  model?: string
  category_id?: string
  description_category_id?: string
  category_path?: string
  category_attribute_schema?: BackendCategoryAttributeSchema
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
  ai_copy_ready?: boolean
  copy_generated_at?: string
  copy_source?: string
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
  local_platform_categories?: Record<string, unknown>
  workflow_statuses?: Record<string, string>
  created_at?: string
  updated_at?: string
}

export interface BackendPublishPlatformState {
  platform?: string
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
  status?: string
  product_name?: string
  product?: BackendProduct
  config?: Record<string, unknown>
  platforms?: Record<string, BackendPublishPlatformState>
  created_at?: string
  updated_at?: string
}
