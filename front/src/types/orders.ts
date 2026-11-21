import type { BackendOrderCenterFilters, BackendPurchaseProgressView, BackendPurchaseTrackingSummary } from './workflow.generated'
export type OrderCenterFilters = BackendOrderCenterFilters
export function emptyOrderCenterFilters(): Required<Omit<OrderCenterFilters, 'time_zone'>> {
  return {
    shop: '',
    procurement_status: '', declaration_status: '', warehouse_status: '', has_waybill: '',
    domestic_status: '', domestic_carrier: '', receiving_warehouse: '', platform_carrier: '',
    exception_type: '', deadline_from: '', deadline_to: '', deadline_status: '',
  }
}
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
  remote_id?: string
  variant_id?: string
  title: string
  quantity: number
  image_url?: string
}
export interface OrderSnapshot extends OrderAmount {
  shop_name?: string
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
  purchase_tracking?: BackendPurchaseTrackingSummary | null
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
  shops?: { platform: OrderPlatform; account_id: string; name: string }[]
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
  source_offer_id?: string
  source_spec_id?: string
  purchase_block_reason?: string
  specification: string
  sku_url: string
  sku_url_verified: boolean
}
export interface SalesSkuBinding {
  id: string
  platform?: OrderPlatform
  store_identity?: string
  product_id: string
  draft_id: string
  sku_id: string
  seller_sku: string
  remote_id?: string
  variant_id?: string
  publication_id: string
  image_url?: string
  origin?: 'publish' | 'alibaba_api'
  is_default?: boolean
  updated_at?: string
  source: ProcurementSource
}
export interface ResolvedAlibabaSku {
  source_sku_id: string
  source_offer_id: string
  source_spec_id: string
  specification: string
  price: string
  stock: string
  image_url: string
  can_auto_purchase: boolean
  block_reason: string
}
export interface ResolvedAlibabaProduct {
  ok: boolean
  resolve_id: string
  offer_id: string
  product_url: string
  title: string
  supplier: string
  image_url: string
  skus: ResolvedAlibabaSku[]
}
export interface SkuSourceTarget {
  platform: OrderPlatform
  account_id?: string
  seller_sku: string
  remote_id?: string
  variant_id?: string
}
export interface SkuSourceSummary extends SkuSourceTarget {
  default_binding_id: string
  bindings: SalesSkuBinding[]
}
export interface PurchaseItem {
  id: string
  line_key: string
  line_signature: string
  product_id: string
  sku: string
  title: string
  quantity: number
  source: ProcurementSource | null
  image_url: string
}
export interface PurchaseRecord {
  receiving_warehouse?: import('@/api/receivingWarehouse').ReceivingWarehouse | null
  progress?: BackendPurchaseProgressView | null
  id: string
  request_id: string
  source_platform: string
  purchase_order_number: string
  items: PurchaseItem[]
  note: string
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
  product_id: string
  purchased_quantity: number
  remaining_quantity: number
}
export interface OrderDetail {
  ok: boolean
  order: OrderSnapshot
  lines: ProcurementLine[]
  purchases: PurchaseRecord[]
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
