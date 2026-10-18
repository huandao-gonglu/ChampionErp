import type { OrderDetail, PurchaseRecord } from '@/types/orders'
import type { FulfillmentDetail } from '@/types/fulfillment'
import { orderProcessingProgress, procurementProgress } from './orderPresentation'
import { domesticWaybillKey, parcelError } from './purchaseLogistics'

export function purchaseLogisticsLabel(record: PurchaseRecord) {
  const data = record.progress?.data
  if (!data || data.logistics === null) return '暂无物流信息'
  const labels = [...new Set(data.logistics.map(parcel => parcel.status_label).filter(Boolean))]
  return labels.join('、') || '暂无物流信息'
}

export function procurementTask(detail: OrderDetail) {
  const ordered = detail.lines.reduce((sum, item) => sum + item.line.quantity, 0)
  const remaining = detail.lines.reduce((sum, item) => sum + item.remaining_quantity, 0)
  const purchased = detail.lines.reduce((sum, item) => sum + item.purchased_quantity, 0)
  const records = detail.purchases.filter(record => record.status !== 'cancelled')
  const hasWaybill = !!detail.order.fulfillment_summary?.has_domestic_waybill ||
    !!detail.order.purchase_tracking?.has_waybill || records.some(record =>
      record.progress?.data?.logistics?.some(parcel => !!parcel.tracking_number))
  const progress = orderProcessingProgress({ ...detail.order, procurement_status: procurementProgress(detail.lines) })
  const canPurchase = detail.order.state === 'pending_shipment'
  if (canPurchase && remaining > 0) return {
    title: records.length ? '继续采购' : '准备采购', tone: 'amber', action: 'purchase' as const,
    note: `采购已登记 ${purchased}/${ordered} 件 · 尚需采购 ${remaining} 件`,
    footer: `待采购 ${remaining} 件`, next: '核对来源与收货安排后采购',
  }
  if (canPurchase && ordered > 0 && procurementProgress(detail.lines) === 'purchased' &&
      !detail.order.fulfillment_summary?.crossborderbus_order_id) return {
    title: hasWaybill ? '准备跨境报单' : '跟进采购进度', tone: hasWaybill ? 'green' : 'amber',
    action: hasWaybill ? 'fulfillment' as const : 'refresh' as const,
    note: `采购已登记 ${purchased}/${ordered} 件 · ${hasWaybill ? '已有国内运单' : '暂无物流信息'}`,
    footer: '采购已登记齐全', next: hasWaybill ? '下一步：准备包裹、服务和面单' : '查看供应商订单及最新物流信息',
  }
  return {
    title: progress.label, tone: progress.tone, action: 'fulfillment' as const,
    note: progress.note, footer: progress.label, next: '查看订单与履约进度',
  }
}

export interface FulfillmentReadinessItem {
  id: 'receiving' | 'parcels' | 'services' | 'label' | 'validation'
  title: string
  ready: boolean
  note: string
  action: string
}

// 就绪项解释已知缺项；提交权限始终同时遵守后端 blocked_reason。
export function fulfillmentReadiness(value: FulfillmentDetail, yandex: boolean): FulfillmentReadinessItem[] {
  const submitted = !!value.crossborderbus_order_id && value.fulfillment_status !== 'CANCELLED'
  const included = value.parcels.filter(parcel => parcel.included)
  const bills = new Set(included.map(parcel => domesticWaybillKey(parcel.tracking_number)).filter(Boolean))
  const parcelsInvalid = parcelError(value.parcels, true)
  const needsConsolidation = bills.size > 1 && (!value.plan?.consolidation_service_id ||
    !value.plan.service_ids.includes(value.plan.consolidation_service_id))
  const serviceReady = !!value.plan && (submitted || !value.receiving_binding_stale) &&
    (submitted || !yandex || value.override) && !needsConsolidation
  const items: FulfillmentReadinessItem[] = []
  if (!submitted && yandex && (!value.receiving_warehouse || value.receiving_binding_stale)) items.push({
    id: 'receiving', title: '采购收货仓', ready: false,
    note: value.receiving_binding_stale ? '原收货绑定已失效，请重新核对。' : '请先确认采购收货安排。', action: '核对收货安排',
  })
  items.push({
    id: 'parcels', title: '包裹清单', ready: !parcelsInvalid && value.parcels_confirmed,
    note: parcelsInvalid || `已选 ${included.length} 个包裹${value.parcels_confirmed ? '，清单已确认' : '，清单待确认'}`,
    action: !included.length ? '录入包裹' : parcelsInvalid ? '完善包裹' : '确认清单',
  }, {
    id: 'services', title: needsConsolidation ? '合单服务' : '报单方案', ready: serviceReady,
    note: needsConsolidation ? `本次有 ${bills.size} 个国内包裹，需确认仓库合单服务。` :
      serviceReady ? value.selected_services?.map(service => service.name).join('、') || '已保存本单方案' : '请核对合作仓并保存本次报单服务。',
    action: needsConsolidation ? '选择合单服务' : '设置报单方案',
  }, {
    id: 'label', title: '国际面单', ready: !!value.platform_label && !!value.platform_tracking_number,
    note: value.label_error || (value.platform_label ? value.platform_tracking_number || '请补充面单号 / 国际跟踪号' : '尚未获取平台面单'),
    action: value.platform_label || value.label_error ? '重新获取面单' : '获取平台面单',
  })
  const reason = value.blocked_reason
  const explained = items.some(item => !item.ready && (
    item.id === 'receiving' && /收货仓|收货绑定|原绑定/.test(reason) ||
    item.id === 'parcels' && /包裹|运单|件数/.test(reason) && !/服务|合单/.test(reason) ||
    item.id === 'services' && /服务|方案|合单/.test(reason) ||
    item.id === 'label' && (/面单/.test(reason) || !!value.label_error && reason === value.label_error)
  ))
  if (!submitted && reason && !explained) items.push({ id: 'validation', title: '提交校验', ready: false, note: reason, action: '查看详情' })
  return items
}
