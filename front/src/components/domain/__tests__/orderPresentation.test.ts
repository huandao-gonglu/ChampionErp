import { describe, expect, it } from 'vitest'
import { deadline, orderProcessingProgress } from '../orderPresentation'
import type { OrderSnapshot } from '@/types/orders'
import type { FulfillmentSummary } from '@/types/fulfillment'
const now = Date.parse('2026-10-13T08:00:00Z')
describe('发货日期精度', () => {
  it('只有日期时不补造零点，也不显示超时或小时倒计时', () => {
    expect(deadline('2026-10-13', now)).toEqual({ text: '2026-10-13', note: '仅提供日期', urgent: false })
    expect(deadline('2026-10-13', now + 86400000)).toEqual({ text: '2026-10-13', note: '仅提供日期', urgent: false })
  })
  it('没有时区的时分保留平台原文，不按电脑时区推算', () => {
    expect(deadline('2026-10-13T12:30:00', now)).toEqual({ text: '2026-10-13 12:30', note: '平台时间（未提供时区）', urgent: false })
  })
  it('带时区的截止才计算剩余小时', () => {
    expect(deadline('2026-10-13T12:00:00+03:00', now)).toMatchObject({ note: '剩余 1 小时', urgent: true })
  })
  it('缺失或无效日期不能被展示为有效截止', () => {
    for (const value of [undefined, '', 'invalid', '2026-02-30']) expect(deadline(value, now)).toEqual({ text: '未提供', note: '', urgent: false })
  })
})

const order: OrderSnapshot = {
  id: 'yandex:shop:FBS:1', platform: 'yandex', account_id: 'shop', order_id: '1',
  fulfillment: 'FBS', title: '', status: 'PROCESSING', shipping_status: 'READY_TO_SHIP',
  state: 'pending_shipment', amount: '', currency: '', updated_at: '', checked_at: '', items: [],
}
const facts: FulfillmentSummary = {
  fulfillment_status: 'NEW', crossborderbus_order_id: null, busy: false, operation: '',
  create_unknown: false, cancel_requested: false, cancel_rejected: false, error_message: '', label_error: '',
}
describe('V1 处理进度按已确认事实展示', () => {
  it.each([
    ['unpurchased', '待采购登记'], ['partial', '采购登记中'], ['purchased', '采购状态待查询'], ['unknown', '采购待核对'],
  ])('采购 %s 不能被平台备妥状态覆盖', (procurement_status, label) => {
    expect(orderProcessingProgress({ ...order, procurement_status }, facts).label).toBe(label)
  })
  it.each([
    ['FULFILLMENT_CREATED', '已创建预报'], ['WAITING_DOMESTIC_SHIPMENT', '待到仓'],
    ['WAREHOUSE_RECEIVED', '包裹已入库'], ['PACKING', '已打包'], ['SHIPPED', '仓库已发货'],
  ] as const)('仓库 %s 优先于采购记录展示', (fulfillment_status, label) => {
    expect(orderProcessingProgress({ ...order, procurement_status: 'unpurchased' }, { ...facts, crossborderbus_order_id: 99, fulfillment_status }).label).toBe(label)
  })
  it('创建超时不能显示待提交，取消时必须先核实创建', () => {
    const unknown = { ...facts, create_unknown: true, fulfillment_status: 'EXCEPTION' as const }
    expect(orderProcessingProgress(order, unknown).label).toBe('创建待确认')
    expect(orderProcessingProgress({ ...order, state: 'cancelled' }, unknown)).toMatchObject({ label: '取消待确认', note: '先核实创建结果，再取消' })
  })
  it('确认已发货后取消失败或同步错误不能倒退仓库状态', () => {
    const shipped = { ...facts, fulfillment_status: 'SHIPPED' as const, crossborderbus_order_id: 99 }
    expect(orderProcessingProgress(order, { ...shipped, error_message: '请求超时' })).toMatchObject({ label: '仓库已发货', tone: 'red' })
    expect(orderProcessingProgress(order, { ...shipped, cancel_requested: true, cancel_rejected: true })).toMatchObject({ label: '仓库已发货', note: '取消未成功，请联系仓库', tone: 'red' })
    expect(orderProcessingProgress(order, shipped).note).toBe('已同步仓库发货状态')
  })
  it('同步失败保留最近确认的入库进度', () => {
    expect(orderProcessingProgress(order, { ...facts, fulfillment_status: 'WAREHOUSE_RECEIVED', crossborderbus_order_id: 99, error_message: '请求超时' })).toMatchObject({ label: '包裹已入库', note: '保留最近确认进度；同步异常', tone: 'red' })
  })
  it('平台已送达但没有预报单不能判定 ERP 履约完成', () => {
    expect(orderProcessingProgress({ ...order, state: 'delivered' }, facts)).toMatchObject({ label: '无跨境预报' })
    expect(orderProcessingProgress({ ...order, fulfillment: 'FBY' }, facts).label).toBe('平台仓履约')
    expect(orderProcessingProgress(order, { ...facts, fulfillment_status: 'COMPLETED', crossborderbus_order_id: 99 }).label).toBe('履约状态待核对')
  })
  it('取消失败、尚未发送和面单获取失败均有可处理的状态', () => {
    expect(orderProcessingProgress(order, { ...facts, cancel_requested: true, cancel_rejected: true, crossborderbus_order_id: 99 }).label).toBe('取消失败')
    expect(orderProcessingProgress({ ...order, state: 'cancelled' }, facts).label).toBe('已停止预报')
    expect(orderProcessingProgress(order, { ...facts, label_error: '面单托管失败' }).label).toBe('面单获取异常')
  })
})

const purchaseOrder: OrderSnapshot = {
  ...order, procurement_status: 'purchased', purchase_tracking: {
    orders: [{ order_number: '123', status: 'waitbuyerpay', status_label: '等待买家付款' }],
    unknown_count: 0, has_waybill: false, stale: false,
  },
}
describe('采购到报单的状态衔接', () => {
  it('没有运单时显示采购订单状态，不被面单错误遮挡', () => {
    expect(orderProcessingProgress(purchaseOrder, { ...facts, label_error: '面单失败' }).label).toBe('等待买家付款')
    const updated = { ...purchaseOrder, purchase_tracking: { ...purchaseOrder.purchase_tracking!, orders: [{ order_number: '123', status: 'waitsellersend', status_label: '等待卖家发货' }] } }
    expect(orderProcessingProgress(updated, facts).label).toBe('等待卖家发货')
  })
  it('有远端运单或人工登记运单后进入待预报，尚未等同已报单', () => {
    expect(orderProcessingProgress(purchaseOrder, { ...facts, has_domestic_waybill: true }).label).toBe('待预报')
    expect(orderProcessingProgress({ ...purchaseOrder, purchase_tracking: { ...purchaseOrder.purchase_tracking!, has_waybill: true } }, facts).label).toBe('待预报')
  })
  it('已有预报后始终展示仓库状态，不因采购查询失败倒退', () => {
    expect(orderProcessingProgress(purchaseOrder, { ...facts, crossborderbus_order_id: 123, fulfillment_status: 'WAREHOUSE_RECEIVED' }).label).toBe('包裹已入库')
  })
  it('多笔采购不使用其中一笔冒充全部进度，失败保留结果但标记过期', () => {
    const mixed = { ...purchaseOrder, purchase_tracking: { ...purchaseOrder.purchase_tracking!, orders: [...purchaseOrder.purchase_tracking!.orders, { order_number: '456', status: 'waitsellersend', status_label: '等待卖家发货' }] } }
    expect(orderProcessingProgress(mixed, facts)).toMatchObject({ label: '采购进度不一致', note: '1688：等待买家付款、等待卖家发货' })
    expect(orderProcessingProgress({ ...mixed, purchase_tracking: { ...mixed.purchase_tracking, unknown_count: 1 } }, facts).label).toBe('部分采购状态待查询')
    expect(orderProcessingProgress({ ...purchaseOrder, purchase_tracking: { ...purchaseOrder.purchase_tracking!, stale: true } }, facts).note).toContain('保留最近查询结果')
  })
  it('采购取消显示异常，不提前显示待预报', () => {
    expect(orderProcessingProgress({ ...purchaseOrder, purchase_tracking: { ...purchaseOrder.purchase_tracking!, orders: [{ order_number: '123', status: 'cancel', status_label: '交易取消' }] } }, facts)).toMatchObject({ label: '交易取消', tone: 'red' })
  })
})

// 多包裹展示按最近更新时间，不按履约阶段排序。
describe('多包裹按更新时间汇总', () => {
  const packages = [
    { id: 'first', order_numbers: ['123'], company: '圆通', tracking_number: 'YT1', source: 'logistics' as const, status: 'SIGN', status_label: '已签收', updated_at: '2026-10-13T08:00:00Z', stale: false },
    { id: 'second', order_numbers: ['456'], company: '顺丰', tracking_number: 'SF2', source: 'logistics' as const, status: 'TRANSPORT', status_label: '运输中', updated_at: '2026-10-13T09:00:00Z', stale: false },
  ]
  const withPackages = (value = packages) => ({ ...purchaseOrder, purchase_tracking: { ...purchaseOrder.purchase_tracking!, has_waybill: true, packages: value } })
  it('刚更新的运输中优先于较早的已签收，并展示部分修饰', () => {
    expect(orderProcessingProgress(withPackages(), facts)).toMatchObject({ label: '部分运输中', target: 'fulfillment' })
    expect(orderProcessingProgress(withPackages([...packages].reverse()), facts).label).toBe('部分运输中')
  })
  it('全部状态相同直接展示，单包裹继续沿用原有报单进度', () => {
    expect(orderProcessingProgress(withPackages(packages.map(p => ({ ...p, status: 'SIGN', status_label: '已签收' }))), facts).label).toBe('已签收')
    expect(orderProcessingProgress(withPackages([packages[0]!]), facts).label).toBe('待预报')
  })
  it('查询失败不替换已有状态，悬浮明细保留过期标记', () => {
    const state = orderProcessingProgress(withPackages(packages.map(p => ({ ...p, stale: true }))), facts)
    expect(state.label).toBe('部分运输中')
    expect(state.note).toContain('保留最近成功结果')
  })
  it('更晚更新的待发货也按用户规则成为主状态', () => {
    const pending = { id: 'pending', order_numbers: ['789'], company: '', tracking_number: '', source: 'order' as const, status: 'waitsellersend', status_label: '等待卖家发货', updated_at: '2026-10-13T10:00:00Z', stale: false }
    const snapshot = { ...purchaseOrder, purchase_tracking: { ...purchaseOrder.purchase_tracking!, packages: [...packages, pending] } }
    expect(orderProcessingProgress(snapshot, facts).label).toBe('部分待发货')
    expect(orderProcessingProgress(snapshot, { ...facts, crossborderbus_order_id: 99, fulfillment_status: 'SHIPPED' }).label).toBe('仓库已发货')
  })
})
