import { apiClient } from './client'
import type { BackendAlibabaSelfPurchaseOptions, BackendAlibabaSelfPurchaseRecord, BackendAlibabaPurchaseAddresses, BackendAlibabaParsedPurchaseAddress, BackendAlibabaPurchaseAddressFields } from '@/types/workflow.generated'
export type SelfPurchaseRecord = BackendAlibabaSelfPurchaseRecord
export type SelfPurchaseOptions = BackendAlibabaSelfPurchaseOptions
export type PurchaseAddressFields = BackendAlibabaPurchaseAddressFields
export type PurchaseAddressSelection = { address_id: string } | { address: PurchaseAddressFields; address_confirmed: true }

export async function selfPurchaseAddresses(orderId: string, lineKey: string): Promise<BackendAlibabaPurchaseAddresses> {
  return (await apiClient.get<BackendAlibabaPurchaseAddresses>('/api/orders/alibaba-purchase/addresses', { params: { order_id: orderId, line_key: lineKey }, timeout: 35000 })).data
}
export async function parsePurchaseAddress(orderId: string, lineKey: string, text: string): Promise<BackendAlibabaParsedPurchaseAddress> {
  return (await apiClient.post<BackendAlibabaParsedPurchaseAddress>('/api/orders/alibaba-purchase/parse-address', { order_id: orderId, line_key: lineKey, text })).data
}

export async function selfPurchaseOptions(orderId: string, lineKey: string): Promise<SelfPurchaseOptions> {
  return (await apiClient.get<SelfPurchaseOptions>('/api/orders/alibaba-purchase', { params: { order_id: orderId, line_key: lineKey } })).data
}
export async function previewSelfPurchase(body: { order_id: string; line_key: string; candidate_id: string; quantity: number } & PurchaseAddressSelection): Promise<SelfPurchaseRecord> {
  return (await apiClient.post<{ record: SelfPurchaseRecord }>('/api/orders/alibaba-purchase/preview', body, { timeout: 110000 })).data.record
}
export async function createSelfPurchase(previewId: string, payChannel: string): Promise<SelfPurchaseRecord> {
  return (await apiClient.post<{ record: SelfPurchaseRecord }>('/api/orders/alibaba-purchase/create', { preview_id: previewId, pay_channel: payChannel }, { timeout: 110000 })).data.record
}
export async function reconcileSelfPurchase(previewId: string): Promise<SelfPurchaseRecord> {
  return (await apiClient.post<{ record: SelfPurchaseRecord }>('/api/orders/alibaba-purchase/reconcile', { preview_id: previewId }, { timeout: 110000 })).data.record
}
export async function selfPurchaseCashier(previewId: string): Promise<string> {
  return (await apiClient.post<{ url: string }>('/api/orders/alibaba-purchase/cashier', { preview_id: previewId }, { timeout: 35000 })).data.url
}
