import { apiClient } from '@/api/client'
import type { OrderIntegrations, OrdersPage } from '@/types/orders'

export async function fetchOrders(query: {
  platform: string
  state: string
  offset: number
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
