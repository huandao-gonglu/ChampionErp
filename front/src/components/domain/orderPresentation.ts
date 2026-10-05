import type { OrderAmount, OrderPlatform, OrderState } from '@/types/orders'

export const procurementNames: Record<string, string> = {
  unpurchased: '待采购',
  partial: '部分采购',
  purchased: '已采购',
  unknown: '待核对',
}
export function stateTone(state: OrderState) {
  return state === 'pending_shipment'
    ? 'blue'
    : state === 'delivered'
      ? 'green'
      : state === 'unknown'
        ? 'amber'
        : 'neutral'
}
export function procurementTone(status = 'unknown') {
  return status === 'purchased' ? 'green' : 'amber'
}
export function amountLabel(value: OrderAmount, platform: OrderPlatform) {
  return value.amount_breakdown
    ? '商品金额（含平台补贴）'
    : platform === 'yandex'
      ? '付款金额（待同步明细）'
      : '订单金额'
}
export function money(value: string) {
  if (!value || !Number.isFinite(Number(value))) return '—'
  return Number(value).toLocaleString('zh-CN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
}
export function dateTime(value: string) {
  const date = new Date(value)
  return value && Number.isFinite(date.getTime())
    ? date.toLocaleString('zh-CN', {
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
      })
    : '未提供'
}
export function deadline(value: string | undefined, now: number) {
  // 无时区的日期/时间是平台日历信息，不能按用户电脑时区生成截止倒计时。
  const calendar = value?.match(/^(\d{4}-\d{2}-\d{2})(?:T(\d{2}:\d{2})(?::\d{2}(?:\.\d+)?)?)?$/)
  if (calendar) {
    const parsed = new Date(`${value}${calendar[2] ? 'Z' : 'T00:00:00Z'}`)
    if (!Number.isFinite(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== calendar[1])
      return { text: '未提供', note: '', urgent: false }
    return {
      text: calendar[2] ? `${calendar[1]} ${calendar[2]}` : calendar[1],
      note: calendar[2] ? '平台时间（未提供时区）' : '仅提供日期',
      urgent: false,
    }
  }
  const time = value ? Date.parse(value) : NaN
  if (!Number.isFinite(time)) return { text: '未提供', note: '', urgent: false }
  const hours = (time - now) / 3_600_000
  const day = new Date(time).toDateString() === new Date(now).toDateString() ? '今天 ' : ''
  const clock = new Date(time).toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
  return {
    text: day ? day + clock : dateTime(value!),
    note: hours < 0 ? '已超时' : hours < 24 ? `剩余 ${Math.max(1, Math.ceil(hours))} 小时` : '',
    urgent: hours < 6,
  }
}
