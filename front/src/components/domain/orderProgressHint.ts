import type { OrderSnapshot } from '@/types/orders'
import { orderPlatformNames } from '@/types/orders'
import { dateTime, orderProcessingProgress, platformStatusLabel, platformStatusNote } from './orderPresentation'

export interface OrderProgressHintSection {
  heading: string
  lines: string[]
  warning?: string
}
export interface OrderProgressHintContent {
  title: string
  sections: OrderProgressHintSection[]
}

function purchasePackageSections(order: OrderSnapshot): OrderProgressHintSection[] {
  return (order.purchase_tracking?.packages || []).map(item => ({
    heading: `1688 国内包裹 · ${item.status_label}`,
    lines: [item.tracking_number ? `${item.company || '国内包裹'} · ${item.tracking_number}` : '尚无运单',
      `采购单：${item.order_numbers.join('、')}`, `最后成功查询：${dateTime(item.updated_at)}`],
    warning: item.stale ? '查询未更新，保留上次结果' : '',
  }))
}

/** 先解释主状态；ERP 步骤附带国内包裹明细，所有信息均来自当前列表的本地快照。 */
export function orderProgressHint(order: OrderSnapshot, progress = orderProcessingProgress(order)): OrderProgressHintContent {
  const facts = order.fulfillment_summary
  if (progress.sourceKind === 'alibaba' || progress.sourceKind === 'alibaba_erp') {
    const purchase = order.purchase_tracking
    const sections = purchasePackageSections(order)
    if (!sections.length) {
      for (const item of purchase?.orders || []) sections.push({ heading: item.status_label, lines: [`采购单：${item.order_number}`], warning: purchase?.stale ? '查询未更新，保留上次结果' : '' })
    }
    if (purchase?.unknown_count) sections.push({ heading: '采购状态待查询', lines: [`${purchase.unknown_count} 条采购记录尚未查询`] })
    return { title: '1688 采购与包裹状态', sections: sections.length ? sections : [{ heading: progress.label, lines: [progress.note] }] }
  }
  if (progress.sourceKind === 'crossborderbus') {
    const lines = [progress.note]
    if (facts?.crossborderbus_order_id) lines.push(`跨境巴士订单号：${facts.crossborderbus_order_id}`)
    if (facts?.section_name) lines.push(`分区：${facts.section_name}`)
    if (facts?.warehouse_name) lines.push(`仓库：${facts.warehouse_name}`)
    lines.push(`最后成功同步：${dateTime(facts?.last_synced_at || '')}`)
    const sections: OrderProgressHintSection[] = [{ heading: progress.label, lines, warning: facts?.error_message }]
    const packages = facts?.registered_packages || []
    const services = facts?.selected_services || []
    const registered = []
    if (packages.length) registered.push(`国内包裹：${packages.length} 个`, ...packages.map(item => `${item.carrier || '国内包裹'} · ${item.tracking_number}`))
    if (services.length) registered.push(`所选服务：${services.map(item => item.name).join('、')}`)
    const consolidation = services.find(item => item.id === facts?.consolidation_service_id)
    if (consolidation) registered.push(`指定合单服务：${consolidation.name}`)
    if (registered.length) sections.push({ heading: 'ERP 已登记资料', lines: [...registered, '包裹登记和服务选择不代表仓库已收货或已完成服务。'] })
    return { title: '跨境巴士履约状态', sections }
  }
  if (progress.sourceKind === 'platform') {
    const lines = [progress.note, `平台订单状态：${platformStatusLabel(order)}`]
    const note = platformStatusNote(order)
    if (note) lines.push(note)
    if (order.status) lines.push(`平台状态码：${order.status}${order.shipping_status ? ` / ${order.shipping_status}` : ''}`)
    if (order.delivery?.method_name) lines.push(`配送方式：${order.delivery.method_name}`)
    if (order.delivery?.tracking_number) lines.push(`平台运单号：${order.delivery.tracking_number}`)
    if (order.updated_at) lines.push(`平台更新时间：${dateTime(order.updated_at)}`)
    if (order.checked_at) lines.push(`本地同步时间：${dateTime(order.checked_at)}`)
    return { title: `${orderPlatformNames[order.platform]} 平台状态`, sections: [{ heading: progress.label, lines }] }
  }
  return { title: 'ERP 处理进度', sections: [{ heading: progress.label, lines: [progress.note],
    warning: [facts?.error_message, facts?.label_error].filter(Boolean).join('；') }, ...purchasePackageSections(order)] }
}
