import { apiClient } from './client'
import type { Carrier } from './logistics'

export interface YandexWarehouseBinding { warehouse_id: number; carrier: Carrier; shipping_mode: string; enabled: boolean }
export interface YandexPricingOption {
  publish_warehouse_id: number
  publish_warehouse_name: string
  route_id: string
  route_name: string
}
export interface YandexWarehouseSettings {
  ok: boolean
  account: { business_id: string; campaign_id: string; placement_type: string; stock_update_mode: string }
  revision: number
  bindings: YandexWarehouseBinding[]
  binding_warnings: Array<{ warehouse_id: number; message: string }>
  modes: Array<{ carrier: Carrier; value: string; transport: string }>
  warehouses: Array<{ id: number; name: string }>
  routes: Array<{ id: string; name: string; carrier: Carrier; transport: string; service: string; group: string }>
  carriers: Array<{ value: Carrier; label: string }>
  checked_at: string
  error: string
  pricing_options?: YandexPricingOption[]
}
export interface SaveYandexWarehouses {
  business_id: string; campaign_id: string; revision: number; bindings: YandexWarehouseBinding[]
}
export async function fetchYandexWarehouses(refresh = false): Promise<YandexWarehouseSettings> {
  return refresh ? (await apiClient.post<YandexWarehouseSettings>('/api/yandex/warehouses/refresh', {})).data
    : (await apiClient.get<YandexWarehouseSettings>('/api/yandex/warehouses')).data
}
export async function saveYandexWarehouses(body: SaveYandexWarehouses): Promise<YandexWarehouseSettings> {
  return (await apiClient.post<YandexWarehouseSettings>('/api/yandex/warehouses/logistics', body)).data
}
