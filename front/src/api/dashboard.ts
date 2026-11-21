import { apiClient } from '@/api/client'
import type { OrderPlatform, OrderState, OrderSyncStatus } from '@/types/orders'

export type DashboardWindowDays = 7 | 14 | 30

export interface DashboardTrendPoint {
  date: string
  label: string
  orders: number
  pending: number
  shipped: number
  cancelled: number
  by_platform: Record<OrderPlatform, number>
  gmv_by_currency: Record<string, number>
  collected: number
  drafts: number
  published: number
}

export interface DashboardPlatformBreakdown {
  platform: OrderPlatform
  total: number
  pending_shipment: number
  processing: number
  shipped: number
  delivered: number
  cancelled: number
  unknown: number
  window_orders: number
  gmv_by_currency: Record<string, number>
  sync_status: OrderSyncStatus
}

export interface DashboardRecentOrder {
  id: string
  platform: OrderPlatform
  order_id: string
  title: string
  state: OrderState
  amount: string
  currency: string
  updated_at: string
  shipment_deadline: string
  shop_name: string
}

export interface DashboardAnalytics {
  ok: boolean
  generated_at: string
  window_days: number
  time_zone: string
  order_summary: {
    total: number
    window_orders: number
    previous_window_orders: number
    window_orders_delta_pct: number | null
    counts: Partial<Record<OrderState, number>>
    pending_shipment: number
    processing: number
    shipped: number
    delivered: number
    cancelled: number
    fulfillment_rate: number
    overdue_count: number
    urgent_count: number
    unread_alerts: number
    attention_count: number
    window_gmv: Record<string, number>
    total_gmv: Record<string, number>
    sync_status: OrderSyncStatus[]
  }
  platform_breakdown: DashboardPlatformBreakdown[]
  catalog_summary: {
    products_total: number
    window_collected: number
    previous_window_collected: number
    window_collected_delta_pct: number | null
    drafts_total: number
    ready_drafts: number
    published_drafts: number
    drafts_by_platform: Record<OrderPlatform, number>
  }
  online_summary: {
    total: number
    active: number
    paused: number
    attention: number
    by_platform: Record<OrderPlatform, { total: number; active: number; paused: number; attention: number }>
  }
  task_summary: {
    running: number
    queued: number
    completed: number
    failed: number
    attention: number
  }
  trends: DashboardTrendPoint[]
  recent_orders: DashboardRecentOrder[]
}

export async function fetchDashboardAnalytics(
  days: DashboardWindowDays = 14,
  timeZone?: string,
): Promise<DashboardAnalytics> {
  const tz = timeZone || Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
  const { data } = await apiClient.get<DashboardAnalytics>('/api/business-queries/dashboard.analytics', {
    params: { days, time_zone: tz },
    timeout: 12000,
  })
  return data
}
