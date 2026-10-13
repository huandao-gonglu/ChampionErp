import { apiClient } from './client'
import type { BackendOrderChatHistory, BackendOrderChatSent } from '@/types/workflow.generated'

export async function fetchOrderChatHistory(orderId: string): Promise<BackendOrderChatHistory> {
  return (await apiClient.post<BackendOrderChatHistory>('/api/orders/chat/history', { order_id: orderId })).data
}

export async function sendOrderChatMessage(orderId: string, message: string): Promise<BackendOrderChatSent> {
  return (await apiClient.post<BackendOrderChatSent>('/api/orders/chat/send', { order_id: orderId, message })).data
}
