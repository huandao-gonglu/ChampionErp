import { apiClient } from './client'
import type { BusServices, BusSettings, FulfillmentDetail, FulfillmentRule } from '@/types/fulfillment'

export async function fetchBusSettings(): Promise<BusSettings> {
  return (await apiClient.get<BusSettings>('/api/crossborderbus/settings')).data
}
export async function busCommand(action: 'authorize' | 'catalog' | 'delete-rule', body: Record<string, unknown> = {}): Promise<BusSettings> {
  return (await apiClient.post<BusSettings>(`/api/crossborderbus/${action}`, body)).data
}
export async function saveFulfillmentRule(rule: FulfillmentRule): Promise<BusSettings> {
  const body = Object.fromEntries(['id', 'platform', 'account_id', 'fulfillment', 'platform_warehouse_id', 'delivery_method_id', 'delivery_method_name', 'country', 'section_id', 'warehouse_id', 'service_ids', 'compatible_warehouse_ids', 'confirmed', 'auto_submit'].map(key => [key, rule[key as keyof FulfillmentRule]]))
  return (await apiClient.post<BusSettings>('/api/crossborderbus/save-rule', body)).data
}
export async function fetchBusServices(sectionId: number, warehouseId: number): Promise<BusServices> {
  return (await apiClient.get<BusServices>('/api/crossborderbus/services', { params: { section_id: sectionId, warehouse_id: warehouseId } })).data
}
export async function fetchFulfillment(orderId: string): Promise<FulfillmentDetail> {
  return (await apiClient.get<FulfillmentDetail>('/api/orders/fulfillment', { params: { order_id: orderId } })).data
}
export type FulfillmentAction = 'fetch-label' | 'label' | 'parcels' | 'plan' | 'pause' | 'resume' | 'submit' | 'sync' | 'retry' | 'cancel'
export async function fulfillmentCommand(action: FulfillmentAction, orderId: string, revision: number, body: Record<string, unknown> = {}): Promise<FulfillmentDetail> {
  return (await apiClient.post<FulfillmentDetail>(`/api/orders/fulfillment/${action}`, { ...body, order_id: orderId, revision })).data
}

export async function uploadFulfillmentLabel(orderId: string, revision: number, file: File): Promise<string> {
  if (!file.name.toLowerCase().endsWith('.pdf') || file.size > 8 * 1024 * 1024 || !file.size) throw new Error('请选择不超过 8 MB 的平台面单 PDF')
  const encoded = await new Promise<string>((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result).split(',')[1] || '')
    reader.onerror = () => reject(new Error('面单文件读取失败'))
    reader.readAsDataURL(file)
  })
  return (await apiClient.post<{ url: string }>('/api/orders/fulfillment/upload-label', { order_id: orderId, revision, content_base64: encoded })).data.url
}
