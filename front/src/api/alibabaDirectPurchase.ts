import { apiClient } from './client'
import type {
  BackendAlibabaParsedPurchaseAddress,
  BackendAlibabaPaymentAgreement,
  BackendAlibabaPurchaseAddressFields,
  BackendAlibabaPurchaseAddresses,
  BackendDirectPurchaseRecord,
  BackendDirectPurchaseResolvedProduct,
  BackendDirectPurchaseState,
} from '@/types/workflow.generated'

export type DirectPurchaseState = BackendDirectPurchaseState
export type DirectPurchaseRecord = BackendDirectPurchaseRecord
export type DirectPurchaseResolvedProduct = BackendDirectPurchaseResolvedProduct
export type DirectPurchaseAddresses = BackendAlibabaPurchaseAddresses
export type DirectPurchaseParsedAddress = BackendAlibabaParsedPurchaseAddress
export type DirectPurchaseAddressFields = BackendAlibabaPurchaseAddressFields
export type DirectPurchasePaymentAgreement = BackendAlibabaPaymentAgreement

export type DirectPurchaseAddressPayload =
  | { address_id: string }
  | { address: DirectPurchaseAddressFields; address_confirmed: true }

export async function fetchDirectPurchaseState(): Promise<DirectPurchaseState> {
  return (await apiClient.get<DirectPurchaseState>('/api/alibaba/direct-purchase/state')).data
}

export async function fetchDirectPurchaseAddresses(refresh = false): Promise<DirectPurchaseAddresses> {
  return (
    await apiClient.get<DirectPurchaseAddresses>('/api/alibaba/direct-purchase/addresses', {
      params: refresh ? { refresh: '1' } : undefined,
      timeout: 35000,
    })
  ).data
}

export async function resolveDirectPurchaseProduct(
  url: string,
  refresh = false,
): Promise<DirectPurchaseResolvedProduct> {
  return (
    await apiClient.post<DirectPurchaseResolvedProduct>(
      '/api/alibaba/direct-purchase/resolve',
      refresh ? { url, refresh: true } : { url },
      { timeout: 60000 },
    )
  ).data
}

export async function parseDirectPurchaseAddress(text: string): Promise<DirectPurchaseParsedAddress> {
  return (
    await apiClient.post<DirectPurchaseParsedAddress>(
      '/api/alibaba/direct-purchase/parse-address',
      { text },
    )
  ).data
}

export async function previewDirectPurchase(
  body: { resolve_token: string; items: Array<{ sku_id: string; quantity: number }> } & DirectPurchaseAddressPayload,
): Promise<DirectPurchaseRecord> {
  return (
    await apiClient.post<{ record: DirectPurchaseRecord }>(
      '/api/alibaba/direct-purchase/preview',
      body,
      { timeout: 110000 },
    )
  ).data.record
}

export async function createDirectPurchase(previewId: string, payChannel: 'alipay' | 'shegou'): Promise<DirectPurchaseRecord> {
  return (
    await apiClient.post<{ record: DirectPurchaseRecord }>(
      '/api/alibaba/direct-purchase/create',
      { preview_id: previewId, pay_channel: payChannel },
      { timeout: 110000 },
    )
  ).data.record
}

export async function reconcileDirectPurchase(previewId: string): Promise<DirectPurchaseRecord> {
  return (
    await apiClient.post<{ record: DirectPurchaseRecord }>(
      '/api/alibaba/direct-purchase/reconcile',
      { preview_id: previewId },
      { timeout: 110000 },
    )
  ).data.record
}

export async function fetchDirectPurchaseCashierUrl(previewId: string): Promise<string> {
  return (
    await apiClient.post<{ url: string }>(
      '/api/alibaba/direct-purchase/cashier',
      { preview_id: previewId },
      { timeout: 35000 },
    )
  ).data.url
}

export async function checkDirectPurchasePaymentStatus(
  payChannel: 'alipay' | 'shegou',
  refresh = false,
): Promise<DirectPurchasePaymentAgreement> {
  return (
    await apiClient.post<{ agreement: DirectPurchasePaymentAgreement }>(
      '/api/alibaba/direct-purchase/payment-status',
      refresh ? { pay_channel: payChannel, refresh: true } : { pay_channel: payChannel },
      { timeout: 35000 },
    )
  ).data.agreement
}

export async function payDirectPurchase(
  previewId: string,
  confirmedTotalFen: number,
): Promise<DirectPurchaseRecord> {
  return (
    await apiClient.post<{ record: DirectPurchaseRecord }>(
      '/api/alibaba/direct-purchase/pay',
      { preview_id: previewId, confirmed_total_fen: confirmedTotalFen },
      { timeout: 110000 },
    )
  ).data.record
}
