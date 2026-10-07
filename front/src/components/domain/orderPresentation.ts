import { orderStateNames } from '@/types/orders'
import type { OrderAmount, OrderPlatform, OrderSnapshot, OrderState } from '@/types/orders'
import type { ProcurementLine } from '@/types/orders'
import type { FulfillmentSummary } from '@/types/fulfillment'
import { fulfillmentStatusNames } from '@/types/fulfillment'

const yandexProcessingNames: Record<string, string> = {
  STARTED: '备货中',
  PACKAGING: '打包中',
  READY_TO_SHIP: '已备妥',
}

export function platformStatusLabel(
  order: Pick<OrderSnapshot, 'platform' | 'status' | 'shipping_status' | 'state'>
) {
  return orderStateNames[order.state]
}

export function platformStatusNote(
  order: Pick<OrderSnapshot, 'platform' | 'status' | 'shipping_status' | 'state'>
) {
  // 平台备妥不代表供应商发货或跨境仓发货。
  if (order.platform === 'yandex' && order.status === 'PROCESSING')
    return order.shipping_status === 'READY_TO_SHIP'
      ? '平台已备妥，等待交接发货'
      : yandexProcessingNames[order.shipping_status] ? `平台${yandexProcessingNames[order.shipping_status]}` : ''
  return ''
}

export function procurementProgress(lines: ProcurementLine[]) {
  const keys = lines.map(line => line.selection.line_key)
  if (!lines.length || keys.some(key => !key) || new Set(keys).size !== keys.length ||
      lines.some(line => !Number.isInteger(line.line.quantity) || line.line.quantity <= 0))
    return 'unknown'
  if (lines.every(line => line.purchased_quantity >= line.line.quantity)) return 'purchased'
  return lines.some(line => line.purchased_quantity > 0) ? 'partial' : 'unpurchased'
}

export function orderProcessingProgress(order: OrderSnapshot, facts: FulfillmentSummary | null | undefined = order.fulfillment_summary) {
  const progress = (label: string, note: string, tone = 'amber') => ({ label, note, tone })
  const status = facts?.fulfillment_status
  const remote = !!facts?.crossborderbus_order_id
  const cancelled = facts?.cancel_requested || order.state === 'cancelled'
  // 仓库确认发货后，取消冲突或网络错误不能把进度倒退为待提交。
  if (status === 'SHIPPED')
    return progress('仓库已发货', cancelled ? '取消未成功，请联系仓库' : facts?.error_message ? '最近确认已发货；同步异常' : '已同步仓库发货状态', cancelled || facts?.error_message ? 'red' : 'green')
  if (status === 'CANCELLED') return progress('履约已取消', remote ? '仓库已确认取消' : '已停止预报', 'neutral')
  if (cancelled) {
    if (facts?.cancel_rejected) return progress('取消失败', '仓库拒绝取消，请处理', 'red')
    return remote || facts?.create_unknown
      ? progress('取消待确认', facts?.create_unknown ? '先核实创建结果，再取消' : '等待仓库确认')
      : progress('已停止预报', '平台或 ERP 已取消', 'neutral')
  }
  if (facts?.busy && facts.operation === 'create') return progress('正在提交预报', '等待跨境巴士回执')
  if (facts?.create_unknown) return progress('创建待确认', '核实结果期间禁止重复提交', 'red')
  if (facts && remote) {
    if (status === 'EXCEPTION') return progress('履约异常', '查看详情处理仓库异常', 'red')
    if (status === 'COMPLETED' || status === 'NEW') return progress('履约状态待核对', '已有预报单，请同步仓库状态', 'red')
    return progress(fulfillmentStatusNames[facts.fulfillment_status], facts?.error_message ? '保留最近确认进度；同步异常' : '以仓库回传为准', facts?.error_message ? 'red' : 'blue')
  }
  if (['FBO', 'FBY', 'FULFILLMENT'].includes((order.delivery?.fulfillment_model || order.fulfillment).toUpperCase()))
    return progress('平台仓履约', '不进入跨境巴士预报流程', 'neutral')
  if (['shipped', 'delivered'].includes(order.state)) return progress('无跨境预报', '仅有平台状态，未确认仓库履约', 'neutral')
  if (order.state !== 'pending_shipment') return progress('待核对平台状态', '平台待发货后才能预报', 'neutral')
  if (facts?.busy && facts.operation === 'fetch-label') return progress('正在获取面单', '获取成功后继续准备预报')
  if (facts?.error_message || status === 'EXCEPTION') return progress('预报异常', '进入详情处理后重试', 'red')
  if (facts?.label_error) return progress('面单获取异常', '进入详情处理或人工补充', 'red')
  if (order.procurement_status === 'unpurchased') return progress('待采购登记', '在详情中登记实际采购')
  if (order.procurement_status === 'partial') return progress('采购登记中', '尚未覆盖全部商品数量')
  if (order.procurement_status === 'purchased') return progress('待预报', '采购已登记，核对面单与国内包裹')
  return progress('采购待核对', '核对商品明细与采购数量')
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
export function handoverDateTime(value: string, timeZone = Intl.DateTimeFormat().resolvedOptions().timeZone) {
  const date = new Date(value)
  return value && Number.isFinite(date.getTime())
    ? date.toLocaleString('zh-CN', {
        timeZone,
        year: 'numeric', month: '2-digit', day: '2-digit',
        hour: '2-digit', minute: '2-digit', hourCycle: 'h23', timeZoneName: 'shortOffset',
      })
    : '平台未提供'
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
