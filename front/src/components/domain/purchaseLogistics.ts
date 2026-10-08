import type { DomesticParcel, PurchaseLogisticsResults } from '@/types/fulfillment'
import type { OrderDetail, PurchaseRecord } from '@/types/orders'

export const domesticCarriers = ['顺丰', '中通', '圆通', '申通', '韵达', '极兔', '邮政', '京东', '其他']

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
    for (const record of line.records) {
      const choices = purchaseLogisticsChoices(record, results)
      if (!choices.length || parcels.some(parcel => parcel.purchase_record_id === record.id)) continue
      // 多运单无法推断每个包裹对应的规格和数量，交由现有表单选择、核对。
      parcels.push({ id: crypto.randomUUID(), line_key: record.line_key, purchase_record_id: record.id,
        carrier: choices.length === 1 ? choices[0]!.carrier : '',
        tracking_number: choices.length === 1 ? choices[0]!.tracking_number : '', quantity: 1 })
    }
  }
  return parcels
}
