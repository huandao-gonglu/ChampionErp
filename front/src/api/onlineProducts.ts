import { apiClient } from '@/api/client'
import type { YandexGuaranteePeriod } from '@/utils/yandexWarranty'

export type OnlinePlatform = 'mercadolibre' | 'ozon' | 'yandex'
export type OnlineOperation = 'price' | 'stock' | 'content' | 'sale_state' | 'archive'
export interface PriceScope { cached_at?: string; observed_at?: string; read_error?: string; id: string; label: string; amount: string | null; currency: string; kind: string; writable: boolean; reason: string }
export interface StockScope { cached_at?: string; observed_at?: string; read_error?: string; id: string; label: string; quantity: number | null; writable: boolean; reason: string; campaign_id?: string; raw_status?: string; sale_state?: string }
export interface BuyerLink { label: string; url: string; site_id: string }
export interface PlatformIssue { severity: 'error' | 'warning'; source: string; code: string; message: string; comment: string }
export interface SourceImageOption {
  asset_id: string; fingerprint: string; preview_url: string; existing_picture_id: string; existing_url: string
}
export interface SourceImageSelection {
  ok: boolean; local_product_id: string; local_draft_id: string; images: SourceImageOption[]; reason: string
}
export interface OnlineListing {
  id: string; platform: OnlinePlatform; account_id: string; remote_id: string; model: string; seller_sku: string; title: string; thumbnail: string
  buyer_links: BuyerLink[]
  details_state: 'pending' | 'unloaded' | 'ready' | 'failed'
  sales_checked_at?: string
  basic_synced_at?: string
  raw_status: string; sale_state: string; raw_sub_status: string[]; prices: PriceScope[]; stocks: StockScope[]; version: string; synced_at: string; status_checked_at: string; errors: string[]; desired_sale_state: string
  platform_issues: PlatformIssue[]
  archived?: boolean
  markets: Array<{ name?: string; raw_sub_status?: string[]; id: string; site_id: string; logistic_type: string; raw_status: string; price: string | null; currency: string }>
  content: { guarantee_period?: YandexGuaranteePeriod; title?: string; description?: string; pictures?: Array<string | {id: string; url: string}>; attributes?: Array<Record<string, unknown>>; attribute_names_error?: string }
  capabilities: Record<Exclude<OnlineOperation, 'archive'>, {enabled: boolean; reason: string; scope: string; fields: string[]}> & {archive?: {enabled: boolean; reason: string; scope: string; fields: string[]}}
}
export interface OnlineJob {
  id: string; operation: string; platform: OnlinePlatform; status: string; created_at: string; updated_at: string; target_id: string
  request: Record<string, unknown>
  result: { last_confirmation_at?: number; before?: {title?:string; seller_sku?:string}; platform_confirmation?: {note?:string; pending?:boolean}; platform_errors?: unknown[]; read_errors?: string[]; error?: string; completed?: number; failed?: number; created?: number; updated?: number; discovery_complete?: boolean; phase?: 'catalog' | 'basic' | 'details' | 'complete'; discovered?: number; changes?: Record<string, unknown>; confirmation?: Record<string, boolean>; evidence?: {synced_at: string; checked_at?: string; version?: string; observed?: Record<string, unknown>}; resolution?: {closed_at: string; acknowledged_unconfirmed: boolean}; items?: Array<{remote_id: string; status: string; error?: string}> }
}
export interface OnlineFeedbackSummary { affected_sku_count: number; error_count: number; warning_count: number }
export interface OnlineBatchPreview {
  items: Array<{listing_id:string; version:string; seller_sku:string; title:string; group_id:string}>
  errors: Array<{listing_id:string; seller_sku:string; error:string}>
  product_count: number
  scope: string
}
export interface OnlineProductGroup {
  id: string; title: string; kind: 'group' | 'single'; item_ids: string[]; total_count: number
  feedback_summary: OnlineFeedbackSummary
}
export interface OnlinePage {
  sales_due_ids?: string[]
  warehouses?: Array<{id:string; name:string; campaign_id:string}>
  items: OnlineListing[]; groups: OnlineProductGroup[]; total: number; listing_total: number; page: number; per_page: number; account_id: string; store_name: string; state: string; markets: string[]; statuses: string[]
  summary: { total: number; active: number; paused: number; attention: number }; latest_sync: OnlineJob | null; jobs: OnlineJob[]
}
function checked<T>(data: T & {ok?: boolean; error?: string}): T {
  if (data.ok === false) throw new Error(data.error || '在线商品请求失败')
  return data
}
export async function fetchOnlineProducts(params: Record<string, string | number>): Promise<OnlinePage> {
  return checked((await apiClient.get('/api/online-products', {params: {...params, view: 'list'}})).data)
}
export async function fetchYandexShopNames(accountId: string): Promise<Record<string, string>> {
  const { data } = await apiClient.get('/api/online-products/yandex-shop-names', {params: {account_id: accountId}})
  return checked<{names: Record<string, string>}>(data).names
}
export async function fetchOnlineDetail(id: string): Promise<OnlineListing> {
  return checked<{item: OnlineListing}>((await apiClient.get('/api/online-products', {params: {id}})).data).item
}
export async function fetchOnlineSourceImages(listingId: string): Promise<SourceImageSelection> {
  return checked((await apiClient.get('/api/online-products/source-images', {params: {listing_id: listingId}})).data)
}

export interface OnlineAttributeOption { value: string; dictionary_value_id: string }
export interface OnlineAttributeDefinition {
  id: string; name: string; required: boolean; read_only: boolean; managed_by: string
  value_type: string; value_mode: string; allow_custom_values: boolean; is_dictionary: boolean
  is_collection: boolean; max_value_count: number | null; default_unit_id: string
  unit_options: Array<{id: string; name: string}>; options: OnlineAttributeOption[]
  has_more_values: boolean; format_hint: string
  max_length?: number | null; min_value?: string | null; max_value?: string | null
}
export interface OnlineAttributeRow { id: string; parameterId: number; value: string; valueId?: string; unitId?: number }
interface AttributePage { category_id: string; attributes: OnlineAttributeDefinition[]; has_more: boolean; next_cursor: string }
interface AttributeValuePage { attribute_id: string; values: OnlineAttributeOption[]; has_more: boolean; next_cursor: string }
interface AttributeResponse<T> { ok: boolean; listing_id: string; version: string; page: T; current_values: OnlineAttributeRow[] }
export async function fetchOnlineAttributes(listingId: string, version: string, cursor = '') {
  return checked((await apiClient.get<AttributeResponse<AttributePage>>('/api/online-products/attributes',
    {params: {listing_id: listingId, version, cursor, limit: 100}})).data)
}
export async function fetchOnlineAttributeValues(listingId: string, version: string, attributeId: string, query = '', cursor = '') {
  return checked((await apiClient.get<AttributeResponse<AttributeValuePage>>('/api/online-products/attributes',
    {params: {listing_id: listingId, version, attribute_id: attributeId, query, cursor, limit: 50}})).data)
}
export async function refreshOnlineSales(accountId: string, listingIds: string[]) {
  return checked((await apiClient.post('/api/online-products/refresh-sales', {account_id: accountId, listing_ids: listingIds})).data)
}
export async function refreshOnlineStatus(listingId: string): Promise<OnlineListing> {
  return checked<{item: OnlineListing}>((await apiClient.post('/api/online-products/refresh-status', {listing_id: listingId})).data).item
}
export async function refreshOnlineDetail(listingId: string): Promise<OnlineListing> {
  return checked<{item: OnlineListing}>((await apiClient.post('/api/online-products/refresh-detail', {listing_id: listingId})).data).item
}
export async function onlineAction(action: string, body: Record<string, unknown>): Promise<OnlineJob> {
  return checked<{job: OnlineJob}>((await apiClient.post(`/api/online-products/${action}`, body)).data).job
}
export async function previewOnlineBatch(body: Record<string, unknown>): Promise<OnlineBatchPreview> {
  return checked((await apiClient.post('/api/online-products/change-batch/preview', body)).data)
}
export async function submitOnlineBatch(body: Record<string, unknown>): Promise<OnlineJob[]> {
  return checked<{jobs:OnlineJob[]}>((await apiClient.post('/api/online-products/change-batch', body)).data).jobs
}
