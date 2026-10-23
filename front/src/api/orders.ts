import { ORDER_PAGE_SIZE } from '@/types/orders'
import { apiClient } from '@/api/client'
import type { OrderIntegrations, OrdersPage, OrderSummary, OrderDetail, OrderAddressNote, OrderCenterFilters } from '@/types/orders'
export async function syncPurchase(orderId: string, recordId: string, signal?: AbortSignal): Promise<OrderDetail> {
  return (await apiClient.post<OrderDetail>('/api/orders/purchase-sync', {
    order_id: orderId, record_id: recordId,
  }, { timeout: 110000, signal })).data
}

export async function fetchOrders(query: {
  platform: string
  state: string
  offset: number
  q?: string
} & OrderCenterFilters): Promise<OrdersPage> {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries({ ...query, limit: ORDER_PAGE_SIZE })) {
    if (value !== undefined && value !== '') params.set(key, String(value))
  }
  if (query.deadline_from || query.deadline_to || query.deadline_status) {
    params.set('time_zone', query.time_zone || Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC')
  }
  return (await apiClient.get<OrdersPage>(`/api/orders?${params}`, { timeout: 10000 })).data
}
export async function fetchOrderIntegrations(): Promise<OrderIntegrations> {
  return (await apiClient.get<OrderIntegrations>('/api/orders/integrations')).data
}
export async function orderCommand(
  action: 'sync' | 'retry' | 'acknowledge' | 'configure',
  body: Record<string, unknown>
) {
  return (await apiClient.post(`/api/orders/${action}`, body)).data
}

export async function fetchOrderSummary(): Promise<OrderSummary> {
  return (await apiClient.get<OrderSummary>('/api/orders/summary', { timeout: 10000 })).data
}
export async function fetchOrderDetail(orderId: string): Promise<OrderDetail> {
  return (
    await apiClient.get<OrderDetail>('/api/orders/detail', {
      params: { order_id: orderId },
      timeout: 10000,
    })
  ).data
}
export async function procurementCommand(
  action: 'select-source' | 'record-purchase' | 'cancel-purchase',
  body: Record<string, unknown>
): Promise<OrderDetail> {
  return (await apiClient.post<OrderDetail>(`/api/orders/${action}`, body)).data
}

export async function fetchOrderAddressNote(orderId: string, shipmentId: string, address: string, signal?: AbortSignal): Promise<OrderAddressNote> {
  return (await apiClient.get<OrderAddressNote>('/api/orders/address-note', {
    params: { order_id: orderId, shipment_id: shipmentId, address }, timeout: 10000, signal,
  })).data
}
export async function saveOrderAddressNote(body: {
  order_id: string; shipment_id: string; address_key: string; revision: number; note: string
}): Promise<OrderAddressNote> {
  return (await apiClient.post<OrderAddressNote>('/api/orders/address-note', body, { timeout: 10000 })).data
}
