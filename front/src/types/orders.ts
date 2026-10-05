export type OrderPlatform = 'mercadolibre' | 'ozon' | 'yandex'
export type OrderState =
  'pending_shipment' | 'processing' | 'shipped' | 'delivered' | 'cancelled' | 'unknown'
export interface OrderSnapshot {
  id: string
  platform: OrderPlatform
  account_id: string
  order_id: string
  fulfillment: string
  title: string
  status: string
  shipping_status: string
  state: OrderState
  amount: string
  currency: string
  updated_at: string
  checked_at: string
  items: { sku: string; title: string; quantity: number }[]
}
export interface OrderNotification {
  id: number
  platform: OrderPlatform
  topic: string
  status: 'queued' | 'running' | 'retry' | 'failed' | 'done'
  attempts: number
  error: string
  received_at: string
  next_attempt: number
}
export interface OrderAlert {
  id: number
  platform: OrderPlatform
  order_id: string
  title: string
  created_at: string
}
export interface OrdersPage {
  ok: boolean
  items: OrderSnapshot[]
  total: number
  counts: Partial<Record<OrderState, number>>
  notifications: OrderNotification[]
  alerts: OrderAlert[]
  unread: number
  latest_alert_id: number
}
export interface OrderIntegrations {
  public_url: string
  platforms: {
    platform: OrderPlatform
    account_id: string
    configured: boolean
    callback_url: string
    last_received: string
  }[]
}
export const orderPlatformNames: Record<OrderPlatform, string> = {
  mercadolibre: 'Mercado Libre',
  ozon: 'Ozon',
  yandex: 'Yandex',
}
export const orderStateNames: Record<OrderState, string> = {
  pending_shipment: '待发货',
  processing: '处理中',
  shipped: '已发货',
  delivered: '已送达',
  cancelled: '已取消 / 退回',
  unknown: '状态待确认',
}
