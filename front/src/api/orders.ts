import { apiClient } from '@/api/client'
import type { OrderIntegrations, OrdersPage, OrderSummary, OrderDetail } from '@/types/orders'

export async function fetchOrders(query: {
  platform: string
  state: string
  offset: number
  q?: string
}): Promise<OrdersPage> {
  const params = new URLSearchParams({ ...query, offset: String(query.offset), limit: '50' })
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
