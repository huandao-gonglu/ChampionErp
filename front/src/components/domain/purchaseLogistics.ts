import type { DomesticParcel, PurchaseLogisticsResults } from '@/types/fulfillment'
import type { OrderDetail, PurchaseRecord } from '@/types/orders'

export const domesticCarriers = ['顺丰', '中通', '圆通', '申通', '韵达', '极兔', '邮政', '京东', '其他']

export interface DomesticParcelGroup {
  id: string
  tracking_number: string
  carrier: string
  carrierConflict: boolean
  items: DomesticParcel[]
  quantity: number
}

export function domesticWaybillKey(trackingNumber: string) {
  return trackingNumber.trim().toUpperCase()
}

export function unassignedParcelQuantity(order: OrderDetail, parcels: DomesticParcel[], lineKey: string, excludingId = '') {
  const line = order.lines.find(row => row.selection.line_key === lineKey)
  const assigned = parcels.filter(parcel => parcel.line_key === lineKey && parcel.id !== excludingId)
    .reduce((sum, parcel) => sum + (Number(parcel.quantity) || 0), 0)
  return Math.max(0, (line?.line.quantity || 0) - assigned)
}

export function parcelQuantityError(order: OrderDetail, parcels: DomesticParcel[]) {
  if (parcels.some(parcel => !Number.isInteger(parcel.quantity) || parcel.quantity < 1))
    return '请填写大于零的整数商品数量。'
  for (const line of order.lines) {
    const items = parcels.filter(parcel => parcel.line_key === line.selection.line_key)
    const quantity = items.reduce((sum, parcel) => sum + parcel.quantity, 0)
    if (quantity <= line.line.quantity) continue
    const locations = items.map(item => `${domesticWaybillKey(item.tracking_number) || '未填单号'}：${item.quantity} 件`).join('；')
    return `SKU ${line.line.sku} 销售需要 ${line.line.quantity} 件，包裹已分配 ${quantity} 件（${locations}）。请修改现有商品数量，或移除重复明细。`
  }
  return ''
}

/** 运单代表实际包裹；商品分配保留原 ID、数量和采购关联，不去掉商品行。 */
export function groupDomesticParcels(parcels: DomesticParcel[]): DomesticParcelGroup[] {
  const groups = new Map<string, DomesticParcel[]>()
  for (const parcel of parcels) {
    const number = domesticWaybillKey(parcel.tracking_number)
    // 尚未填写单号的新包裹各自保留，不能把空值当作同一运单。
    const key = number ? `waybill:${number}` : `new:${parcel.id}`
    const items = groups.get(key) || []
    items.push(parcel)
    groups.set(key, items)
  }
  return [...groups.values()].map(items => {
    const carriers = [...new Set(items.map(item => item.carrier.trim()).filter(Boolean))]
    return {
      id: items[0]!.id, tracking_number: domesticWaybillKey(items[0]!.tracking_number),
      carrier: carriers.length === 1 ? carriers[0]! : '', carrierConflict: carriers.length > 1,
      items, quantity: items.reduce((sum, item) => sum + item.quantity, 0),
    }
  })
}

export function purchaseLogisticsChoices(record: PurchaseRecord, results: PurchaseLogisticsResults) {
  const result = results[record.id]
  if (record.status !== 'purchased' || !result?.ok || result.record_id !== record.id || result.order_number !== record.purchase_order_number.trim()) return []
  const choices = new Map<string, { carrier: string; tracking_number: string }>()
  for (const parcel of result.logistics || []) {
    const tracking = parcel.tracking_number.trim()
    if (!tracking) continue
    const company = parcel.company.trim()
    const carrier = domesticCarriers.find(name => name !== '其他' && company.includes(name)) || company
    choices.set(`${carrier}:${tracking}`, { carrier, tracking_number: tracking })
  }
  return [...choices.values()]
}

export function seedDomesticParcels(order: OrderDetail, existing: DomesticParcel[], results: PurchaseLogisticsResults): DomesticParcel[] {
  const parcels = existing.map(parcel => ({ ...parcel }))
  for (const line of order.lines) {
    // 已经分配过的商品只展示保存事实，不因某条采购缺少关联而在打开时追加明细。
    if (existing.some(parcel => parcel.line_key === line.selection.line_key)) continue
    for (const record of line.records) {
      const choices = purchaseLogisticsChoices(record, results)
      if (!choices.length || parcels.some(parcel => parcel.purchase_record_id === record.id)) continue
      // 已有直接包裹也占用销售数量，不能按缺少采购关联再重复补入一件。
      const remaining = unassignedParcelQuantity(order, parcels, line.selection.line_key)
      if (!remaining) continue
      // 多运单无法推断每个包裹对应的规格和数量，交由现有表单选择、核对。
      parcels.push({ id: crypto.randomUUID(), line_key: record.line_key, purchase_record_id: record.id,
        carrier: choices.length === 1 ? choices[0]!.carrier : '',
        tracking_number: choices.length === 1 ? choices[0]!.tracking_number : '', quantity: 1 })
    }
  }
  return parcels
}
