import { apiClient } from './client'
import type { PurchaseAddressFields } from './alibabaSelfPurchase'
import type { BackendAlibabaParsedPurchaseAddress } from '@/types/workflow.generated'

export type Carrier = 'CEL' | 'UNI' | 'GBS_GUOO' | 'GUOO_GTA' | 'URAL' | 'ATC'
export interface LogisticsRule { platform: 'yandex'; carrier: Carrier }
export interface LogisticsAddressInput {
  id: string; revision: number; warehouse_id: number; name: string; address: PurchaseAddressFields
  enabled: boolean; is_default: boolean; remark: string; rules: LogisticsRule[]
}
export interface LogisticsAddress extends LogisticsAddressInput { warehouse_name: string; updated_at: string; migrated: boolean }
export interface LogisticsSettings {
  ok: boolean; authorized: boolean
  warehouses: Array<{ id: number; name: string; code: string; sections: Array<{ id: number; name: string; platform: string }> }>
  addresses: LogisticsAddress[]
  carriers: Array<{ value: Carrier; label: string; platform: 'yandex' }>
}
export async function fetchLogisticsSettings(refresh = false): Promise<LogisticsSettings> {
  return refresh ? (await apiClient.post<LogisticsSettings>('/api/logistics/refresh', {})).data
    : (await apiClient.get<LogisticsSettings>('/api/logistics/settings')).data
}
export async function saveLogisticsAddress(value: LogisticsAddressInput): Promise<LogisticsSettings> {
  return (await apiClient.post<LogisticsSettings>('/api/logistics/address', value)).data
}
export async function parseLogisticsAddress(text: string): Promise<BackendAlibabaParsedPurchaseAddress> {
  return (await apiClient.post<BackendAlibabaParsedPurchaseAddress>('/api/logistics/parse-address', { text })).data
}
export function emptyLogisticsAddress(warehouseId: number): LogisticsAddressInput {
  return { id: '', revision: 0, warehouse_id: warehouseId, name: '', enabled: true, is_default: false, remark: '', rules: [],
    address: { fullName: '', mobile: '', phone: '', provinceText: '', cityText: '', areaText: '', townText: '', address: '', postCode: '' } }
}
