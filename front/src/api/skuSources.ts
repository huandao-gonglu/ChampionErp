import { apiClient } from '@/api/client'
import type {
  OrderDetail,
  OrderPlatform,
  ResolvedAlibabaProduct,
  SalesSkuBinding,
  SkuSourceSummary,
  SkuSourceTarget,
} from '@/types/orders'

export interface SkuSourceMutationResponse {
  ok: boolean
  binding?: SalesSkuBinding
  removed?: boolean
  bindings: SalesSkuBinding[]
  order_detail?: OrderDetail
}

export async function resolveAlibabaSource(url: string): Promise<ResolvedAlibabaProduct> {
  return (
    await apiClient.post<ResolvedAlibabaProduct>(
      '/api/sku-sources/resolve-1688',
      { url },
      { timeout: 30000 },
    )
  ).data
}

export async function querySkuSources(items: SkuSourceTarget[]): Promise<SkuSourceSummary[]> {
  if (!items.length) return []
  const { data } = await apiClient.post<{ ok: boolean; items: SkuSourceSummary[] }>(
    '/api/sku-sources/query',
    { items },
    { timeout: 15000 },
  )
  return data.items || []
}

export async function bindSkuSource(body: {
  platform: OrderPlatform
  account_id?: string
  seller_sku: string
  remote_id?: string
  variant_id?: string
  resolve_id: string
  source_sku_id: string
  set_default?: boolean
  order_id?: string
  line_key?: string
  revision?: number
}): Promise<SkuSourceMutationResponse> {
  return (await apiClient.post<SkuSourceMutationResponse>('/api/sku-sources/bind', body, { timeout: 15000 })).data
}

export async function setDefaultSkuSource(body: {
  platform: OrderPlatform
  account_id?: string
  seller_sku: string
  remote_id?: string
  variant_id?: string
  binding_id: string
  order_id?: string
  line_key?: string
  revision?: number
}): Promise<SkuSourceMutationResponse> {
  return (await apiClient.post<SkuSourceMutationResponse>('/api/sku-sources/set-default', body, { timeout: 15000 })).data
}

export async function removeSkuSource(body: {
  platform: OrderPlatform
  account_id?: string
  seller_sku: string
  binding_id: string
}): Promise<SkuSourceMutationResponse> {
  return (await apiClient.post<SkuSourceMutationResponse>('/api/sku-sources/remove', body, { timeout: 15000 })).data
}
