import type { BackendPurchaseProgressView } from './workflow.generated'
export const ORDER_PAGE_SIZE = 9
export type OrderPlatform = 'mercadolibre' | 'ozon' | 'yandex'
export type OrderState =
  'pending_shipment' | 'processing' | 'shipped' | 'delivered' | 'cancelled' | 'unknown'
export interface OrderAmount {
  amount: string
  currency: string
  amount_breakdown?: {
    payment: string
    subsidy: string
    cashback: string
  } | null
}
export interface OrderLine extends OrderAmount {
  sku: string
  title: string
  quantity: number
  image_url?: string
}
export interface OrderSnapshot extends OrderAmount {
  delivery?: import('./fulfillment').DeliverySource
  handover?: OrderHandoverSnapshot | null
  id: string
  platform: OrderPlatform
  account_id: string
  order_id: string
  fulfillment: string
  title: string
  status: string
  shipping_status: string
  state: OrderState
  updated_at: string
  checked_at: string
  shipment_deadline?: string
  procurement_status?: string
  fulfillment_summary?: import('./fulfillment').FulfillmentSummary | null
  items: OrderLine[]
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
export interface OrderSyncStatus {
  platform: OrderPlatform
  status: 'idle' | 'queued' | 'running' | 'retry' | 'failed' | 'done' | 'blocked' | 'cooldown'
  error: string
  last_success_at: string
  next_attempt: number
}
export interface OrdersPage {
  sync_status?: OrderSyncStatus[]
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

export interface OrderSummary {
  sync_status?: OrderSyncStatus[]
  ok: boolean
  counts: Partial<Record<OrderState, number>>
  unread: number
  alerts: OrderAlert[]
  latest_alert_id: number
  attention_count: number
  recent: Pick<
    OrderSnapshot,
    'id' | 'platform' | 'order_id' | 'title' | 'state' | 'amount' | 'currency'
  >[]
}
export interface ProcurementSource {
  supplier: string
  source_platform: string
  product_url: string
  source_sku_id: string
  specification: string
  sku_url: string
  sku_url_verified: boolean
}
export interface SalesSkuBinding {
  id: string
  product_id: string
  draft_id: string
  sku_id: string
  seller_sku: string
  publication_id: string
  source: ProcurementSource
}
export interface PurchaseRecord {
  progress?: BackendPurchaseProgressView | null
  id: string
  line_key: string
  request_id: string
  quantity: number
  purchase_order_number: string
  source: ProcurementSource
  created_at: string
  status: 'purchased' | 'cancelled'
  cancelled_at: string
}
export interface ProcurementLine {
  line: OrderLine
  selection: {
    line_key: string
    revision: number
    status: 'unmatched' | 'matched' | 'ambiguous' | 'confirmed'
    source: ProcurementSource | null
    candidates: SalesSkuBinding[]
    reason: string
  }
  records: PurchaseRecord[]
  purchased_quantity: number
  remaining_quantity: number
}
export interface OrderDetail {
  ok: boolean
  order: OrderSnapshot
  lines: ProcurementLine[]
}

export interface HandoverWarehouse {
  id: string
  name: string
  address: string
}
export interface OrderHandoverShipment {
  shipment_id: string
  shipment_type: string
  status: string
  planned_from: string
  planned_to: string
  origin: HandoverWarehouse | null
  destination: HandoverWarehouse | null
}
export interface OrderHandoverSnapshot {
  state: 'ready' | 'unavailable'
  message: string
  checked_at: string
  shipments: OrderHandoverShipment[]
}

export interface OrderAddressNote {
  ok: boolean
  address_key: string
  address: string
  note: string
  revision: number
  updated_at: string
}
