import { apiClient } from '@/api/client'

export type OnlinePlatform = 'mercadolibre' | 'ozon' | 'yandex'
export type OnlineOperation = 'price' | 'stock' | 'content' | 'sale_state'
export interface PriceScope { id: string; label: string; amount: string | null; currency: string; kind: string; writable: boolean; reason: string }
export interface StockScope { id: string; label: string; quantity: number | null; writable: boolean; reason: string }
export interface BuyerLink { label: string; url: string; site_id: string }
export interface SourceImageOption {
  asset_id: string; fingerprint: string; preview_url: string; existing_picture_id: string; existing_url: string
}
export interface SourceImageSelection {
  ok: boolean; local_product_id: string; local_draft_id: string; images: SourceImageOption[]; reason: string
}
export interface OnlineListing {
  id: string; platform: OnlinePlatform; account_id: string; remote_id: string; model: string; seller_sku: string; title: string; thumbnail: string
  buyer_links: BuyerLink[]
  details_state: 'pending' | 'ready' | 'failed'
  raw_status: string; sale_state: string; raw_sub_status: string[]; prices: PriceScope[]; stocks: StockScope[]; version: string; synced_at: string; status_checked_at: string; errors: string[]; desired_sale_state: string
  markets: Array<{ id: string; site_id: string; logistic_type: string; raw_status: string; price: string | null; currency: string }>
  content: { title?: string; description?: string; pictures?: Array<string | {id: string; url: string}>; attributes?: Array<Record<string, unknown>> }
  capabilities: Record<OnlineOperation, {enabled: boolean; reason: string; scope: string; fields: string[]}>
}
export interface OnlineJob {
  id: string; operation: string; platform: OnlinePlatform; status: string; created_at: string; updated_at: string; target_id: string
  request: Record<string, unknown>
  result: { automatic_confirmation_pending?: boolean; next_confirmation_at?: number; last_confirmation_at?: number; before?: {title?:string}; platform_confirmation?: {note?:string}; platform_errors?: unknown[]; error?: string; completed?: number; failed?: number; created?: number; updated?: number; discovery_complete?: boolean; phase?: 'catalog' | 'details' | 'complete'; discovered?: number; changes?: Record<string, unknown>; confirmation?: Record<string, boolean>; evidence?: {synced_at: string}; items?: Array<{remote_id: string; status: string; error?: string}> }
}
export interface OnlineProductGroup {
  id: string; title: string; kind: 'group' | 'single'; item_ids: string[]; total_count: number
}
export interface OnlinePage {
  items: OnlineListing[]; groups: OnlineProductGroup[]; total: number; listing_total: number; page: number; per_page: number; account_id: string; store_name: string; state: string; markets: string[]; statuses: string[]
  summary: { total: number; active: number; paused: number; attention: number }; latest_sync: OnlineJob | null; jobs: OnlineJob[]
}
function checked<T>(data: T & {ok?: boolean; error?: string}): T {
  if (data.ok === false) throw new Error(data.error || '在线商品请求失败')
  return data
}
export async function fetchOnlineProducts(params: Record<string, string | number>): Promise<OnlinePage> {
  return checked((await apiClient.get('/api/online-products', {params})).data)
}
export async function fetchOnlineDetail(id: string): Promise<OnlineListing> {
  return checked<{item: OnlineListing}>((await apiClient.get('/api/online-products', {params: {id}})).data).item
}
export async function fetchOnlineSourceImages(listingId: string): Promise<SourceImageSelection> {
  return checked((await apiClient.get('/api/online-products/source-images', {params: {listing_id: listingId}})).data)
}
export async function refreshOnlineStatus(listingId: string): Promise<OnlineListing> {
  return checked<{item: OnlineListing}>((await apiClient.post('/api/online-products/refresh-status', {listing_id: listingId})).data).item
}
export async function onlineAction(action: string, body: Record<string, unknown>): Promise<OnlineJob> {
  return checked<{job: OnlineJob}>((await apiClient.post(`/api/online-products/${action}`, body)).data).job
}
