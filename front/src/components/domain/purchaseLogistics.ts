import type { DomesticParcel } from '@/types/fulfillment'

export const domesticCarriers = ['顺丰', '中通', '圆通', '申通', '韵达', '极兔', '邮政', '京东', '其他']
export function domesticWaybillKey(value: string) { return value.trim().toUpperCase() }
export function parcelError(parcels: DomesticParcel[], confirmed: boolean) {
  const bills = new Set<string>()
  for (const parcel of parcels) {
    const bill = domesticWaybillKey(parcel.tracking_number)
    if (!bill) return '请填写国内快递单号。'
    if (bills.has(bill)) return `运单 ${bill} 已登记，请直接编辑已有包裹。`
    bills.add(bill)
    if (parcel.quantity !== null && (!Number.isInteger(parcel.quantity) || parcel.quantity < 1)) return '商品件数须为大于零的整数，未知可留空。'
    if (confirmed && parcel.included && parcel.quantity === null) return `请补充包裹 ${bill} 的商品件数。`
  }
  if (confirmed && !parcels.some(p => p.included)) return '请至少选择一个参与本次报单的包裹。'
  return ''
}
