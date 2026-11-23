import { apiClient } from './client'
import type { BackendAlibabaSelfPurchaseOptions, BackendAlibabaSelfPurchaseRecord, BackendAlibabaPurchaseAddresses, BackendAlibabaParsedPurchaseAddress, BackendAlibabaPurchaseAddressFields } from '@/types/workflow.generated'
export type SelfPurchaseRecord = BackendAlibabaSelfPurchaseRecord
export type SelfPurchaseOptions = BackendAlibabaSelfPurchaseOptions
export type PurchaseAddressFields = BackendAlibabaPurchaseAddressFields
export type PurchaseAddressSelection = { receiving_revision: number } | { address_id: string } | { address: PurchaseAddressFields; address_confirmed: true }

export async function selfPurchaseAddresses(orderId: string, lineKey: string, refresh = false): Promise<BackendAlibabaPurchaseAddresses> {
  return (await apiClient.get<BackendAlibabaPurchaseAddresses>('/api/orders/alibaba-purchase/addresses', { params: { order_id: orderId, line_key: lineKey, ...(refresh ? { refresh: '1' } : {}) }, timeout: 35000 })).data
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
  const agreement = await selfPurchasePaymentStatus(payChannel)
  if (agreement.channel !== payChannel) throw new Error('支付渠道签约状态不一致，请重新查询')
  if (!agreement.ready) throw new Error(agreement.message)
  const record = (await apiClient.post<{ record: SelfPurchaseRecord }>('/api/orders/alibaba-purchase/create', { preview_id: previewId, pay_channel: payChannel }, { timeout: 110000 })).data.record
  if (record.pay_channel !== payChannel) throw new Error('采购回执的支付渠道不一致，请查询原订单')
  if (record.state === 'created' && record.purchase_record_id && !record.payment) {
    return paySelfPurchase(record.id, record.preview.total_fen)
  }
  return record
}
export type PaymentAgreement = import('@/types/workflow.generated').BackendAlibabaPaymentAgreement
export async function selfPurchasePaymentStatus(payChannel: string, refresh = false): Promise<PaymentAgreement> {
  return (await apiClient.post<{ agreement: PaymentAgreement }>('/api/orders/alibaba-purchase/payment-status', refresh ? { pay_channel: payChannel, refresh: true } : { pay_channel: payChannel }, { timeout: 35000 })).data.agreement
}
export async function paySelfPurchase(previewId: string, confirmedTotalFen: number): Promise<SelfPurchaseRecord> {
  return (await apiClient.post<{ record: SelfPurchaseRecord }>('/api/orders/alibaba-purchase/pay', { preview_id: previewId, confirmed_total_fen: confirmedTotalFen }, { timeout: 110000 })).data.record
}
export async function reconcileSelfPurchase(previewId: string): Promise<SelfPurchaseRecord> {
  return (await apiClient.post<{ record: SelfPurchaseRecord }>('/api/orders/alibaba-purchase/reconcile', { preview_id: previewId }, { timeout: 110000 })).data.record
}
export async function selfPurchaseCashier(previewId: string): Promise<string> {
  return (await apiClient.post<{ url: string }>('/api/orders/alibaba-purchase/cashier', { preview_id: previewId }, { timeout: 35000 })).data.url
}

export type BatchPurchaseOptions = import('@/types/workflow.generated').BackendAlibabaBatchPurchaseOptions
export async function batchPurchaseOptions(orderId: string): Promise<BatchPurchaseOptions> {
  return (await apiClient.get<BatchPurchaseOptions>('/api/orders/alibaba-purchase/batch', { params: { order_id: orderId } })).data
}
export async function previewBatchPurchase(body: { order_id: string; receiving_revision: number; items: { line_key: string; candidate_id: string; quantity: number }[] }): Promise<SelfPurchaseRecord[]> {
  return (await apiClient.post<{ records: SelfPurchaseRecord[] }>('/api/orders/alibaba-purchase/batch-preview', body, { timeout: 110000 })).data.records
}
