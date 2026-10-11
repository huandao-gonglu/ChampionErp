import { apiClient } from './client'
import type { PurchaseAddressFields } from './alibabaSelfPurchase'
import type { Carrier, LogisticsAddress, LogisticsRule } from './logistics'

export interface ReceivingWarehouse {
  revision: number
  section_id: number
  warehouse_id: number
  section_name: string
  warehouse_name: string
  address: PurchaseAddressFields
  transport_service: string
  delivery_key: string
  confirmed_at: string
  address_id?: string
  address_revision?: number
  address_name?: string
  carrier?: Carrier | null
  shipping_service?: string | null
  support_rule?: LogisticsRule | null
}
export interface ReceivingWarehouseView {
  ok: boolean
  order_id: string
  revision: number
  warehouse: ReceivingWarehouse | null
  delivery_key: string
  delivery_name: string
  delivery_evidence: string[]
  blocked_reason: string
  locked: boolean
  reserved: boolean
  can_confirm: boolean
  has_purchases: boolean
  candidates: Array<LogisticsAddress & { section_id: number; frozen: boolean }>
  carrier_detected: Carrier | null
  carrier: Carrier | null
}
export async function fetchReceivingWarehouse(orderId: string): Promise<ReceivingWarehouseView> {
  return (await apiClient.get<ReceivingWarehouseView>('/api/orders/receiving-warehouse', { params: { order_id: orderId } })).data
}
export async function saveReceivingWarehouse(body: {
  order_id: string; revision: number; section_id: number; address_id: string; address_revision: number
  delivery_key: string
  confirmed: boolean; existing_purchase_confirmed: boolean
}): Promise<ReceivingWarehouseView> {
  return (await apiClient.post<ReceivingWarehouseView>('/api/orders/receiving-warehouse', body, { timeout: 35000 })).data
}
