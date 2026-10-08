import type { OrderPlatform } from './orders'
import type { BackendAlibabaPurchaseQueryResult } from './workflow.generated'

export type PurchaseLogisticsResults = Record<string, BackendAlibabaPurchaseQueryResult>

export interface DeliverySource {
  fulfillment_model: string
  warehouse_id: string
  warehouse_name: string
  method_id: string
  method_name: string
  carrier: string
  country: string
  shipment_id: string
  tracking_number: string
  label_url: string
}
export interface FulfillmentPlan {
  section_id: number
  warehouse_id: number
  service_ids: number[]
}
export interface DeliveryChoice {
  platform: OrderPlatform
  account_id: string
  fulfillment: string
  platform_warehouse_id: string
  platform_warehouse_name?: string
  delivery_method_id: string
  delivery_method_name: string
  country: string
}
export interface FulfillmentRule extends FulfillmentPlan, DeliveryChoice {
  id: string
  compatible_warehouse_ids: number[]
  confirmed: boolean
  auto_submit: boolean
}
export interface DomesticParcel {
  id: string
  line_key: string
  purchase_record_id: string
  carrier: string
  tracking_number: string
  quantity: number
}
export interface BusSection {
  section_id: number
  section_name: string
  storehouse_list: { id: number; name: string; code: string }[]
}
export interface BusService { id: number; name: string; gold: number }
export interface BusServices { core_data: BusService[]; optional_data: BusService[] }
export interface BusSettings {
  authorized: boolean
  secret_configured: boolean
  user_name: string
  expires_at: number
  catalog: { sections?: BusSection[]; checked_at?: number }
  rules: FulfillmentRule[]
  delivery_choices: DeliveryChoice[]
}
export type FulfillmentStatus = 'NEW' | 'FULFILLMENT_CREATED' | 'WAITING_DOMESTIC_SHIPMENT' | 'WAREHOUSE_RECEIVED' | 'PACKING' | 'SHIPPED' | 'COMPLETED' | 'EXCEPTION' | 'CANCELLED'
export interface FulfillmentSummary {
  has_domestic_waybill?: boolean
  busy: boolean
  operation: string
  create_unknown: boolean
  cancel_requested: boolean
  cancel_rejected: boolean
  crossborderbus_order_id: number | null
  fulfillment_status: FulfillmentStatus
  error_message: string
  label_error: string
}
export interface FulfillmentDetail extends FulfillmentSummary {
  erp_order_id: string
  revision: number
  editing: boolean
  editable: boolean
  plan_editable: boolean
  update_pending?: boolean
  blocked_reason: string
  last_attempt_at: string
  last_synced_at: string
  next_attempt: number
  platform_label: string
  platform_tracking_number: string
  label_fetch_supported: boolean
  label_fetch_reason: string
  label_attempt_at: string
  country: string
  plan: FulfillmentPlan | null
  override: boolean
  rule: FulfillmentRule | null
  section_name: string
  warehouse_name: string
  delivery: DeliverySource
  parcels: DomesticParcel[]
}
export const fulfillmentStatusNames: Record<FulfillmentStatus, string> = {
  NEW: '待预报', FULFILLMENT_CREATED: '已创建预报', WAITING_DOMESTIC_SHIPMENT: '待到仓',
  WAREHOUSE_RECEIVED: '包裹已入库', PACKING: '已打包', SHIPPED: '仓库已发货',
  COMPLETED: '履约状态待核对', EXCEPTION: '履约异常', CANCELLED: '履约已取消',
}
