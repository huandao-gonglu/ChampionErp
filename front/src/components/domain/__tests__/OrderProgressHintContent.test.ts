import { describe, expect, it } from 'vitest'
import type { OrderSnapshot } from '@/types/orders'
import type { FulfillmentSummary } from '@/types/fulfillment'
import { orderProgressHint } from '../orderProgressHint'

const order: OrderSnapshot = {
  id: '1', order_id: '1', platform: 'yandex', account_id: 'shop', fulfillment: 'FBS', title: '',
  state: 'pending_shipment', status: 'PROCESSING', shipping_status: 'READY_TO_SHIP', amount: '1', currency: 'RUB',
  updated_at: '2026-10-13T08:00:00Z', checked_at: '2026-10-13T09:00:00Z', items: [], procurement_status: 'purchased',
  purchase_tracking: { orders: [{ order_number: '1688-order', status: 'waitsellersend', status_label: '等待卖家发货' }], unknown_count: 0, stale: false, has_waybill: true,
    packages: [{ id: '1', order_numbers: ['1688-order'], company: '圆通', tracking_number: 'YT123', source: 'logistics', status: 'SIGN', status_label: '已签收', updated_at: '2026-10-13T08:00:00Z', stale: false }] },
}
const facts: FulfillmentSummary = {
  fulfillment_status: 'PACKING', crossborderbus_order_id: 99, busy: false, operation: '', create_unknown: false,
  cancel_requested: false, cancel_rejected: false, error_message: '', label_error: '', last_synced_at: '2026-10-13T10:00:00Z',
  warehouse_name: '义乌仓', section_name: 'Yandex', registered_packages: [{ carrier: '圆通', tracking_number: 'YT123' }],
  selected_services: [{ id: 3, name: '合单打包', gold: 2 }], consolidation_service_id: 3,
}
describe('hint 跟随主状态来源', () => {
  it.each(['PACKING', 'SHIPPED', 'EXCEPTION', 'CANCELLED'] as const)('仓库 %s 不混入1688已签收，并区分 ERP 登记资料', fulfillment_status => {
    const hint = orderProgressHint({ ...order, fulfillment_summary: { ...facts, fulfillment_status, error_message: '保留最近仓库结果' } })
    expect(hint.title).toBe('跨境巴士履约状态')
    const text = JSON.stringify(hint)
    for (const value of ['义乌仓', '99', '最后成功同步', 'ERP 已登记资料', '国内包裹：1 个', 'YT123', '指定合单服务：合单打包', '保留最近仓库结果']) expect(text).toContain(value)
    expect(text).not.toContain('已签收')
    expect(text).not.toContain('1688-order')
    expect(text).not.toContain('打包时间')
  })
  it('旧履约没有扩展明细时不编造仓库、服务或事件时间', () => {
    const hint = orderProgressHint({ ...order, fulfillment_summary: { ...facts, warehouse_name: undefined, section_name: undefined, registered_packages: undefined, selected_services: undefined, last_synced_at: undefined } })
    expect(hint.sections).toHaveLength(1)
    expect(hint.sections[0]!.lines).toContain('最后成功同步：未提供')
    expect(JSON.stringify(hint)).not.toContain('undefined')
  })
  it('平台仓履约仅解释平台状态和平台物流信息', () => {
    const hint = orderProgressHint({ ...order, fulfillment: 'FBY', delivery: { fulfillment_model: 'FBY', method_name: '平台配送', tracking_number: 'PL123' } as OrderSnapshot['delivery'] })
    expect(hint.title).toBe('Yandex 平台状态')
    const text = JSON.stringify(hint)
    for (const value of ['平台已备妥', 'PROCESSING / READY_TO_SHIP', '配送方式：平台配送', '平台运单号：PL123', '平台更新时间', '本地同步时间']) expect(text).toContain(value)
    expect(text).not.toContain('YT123')
  })
  it('ERP 待报单和面单异常优先展示步骤与原因，包裹状态单独标明来源', () => {
    const hint = orderProgressHint(order)
    expect(hint.title).toBe('ERP 处理进度')
    expect(hint.sections[0]!.heading).toBe('待报单')
    expect(hint.sections[1]!.heading).toBe('1688 国内包裹 · 已签收')
    expect(hint.sections[1]!.lines).toContain('圆通 · YT123')
    const failed = orderProgressHint({ ...order, fulfillment_summary: { ...facts, crossborderbus_order_id: null, fulfillment_status: 'NEW', label_error: '面单下载失败' } })
    expect(failed.sections[0]!.warning).toBe('面单下载失败')
  })
  it('多包裹待报单提示保留各自状态、运单、查询时间和过期警告', () => {
    const hint = orderProgressHint({ ...order, purchase_tracking: { ...order.purchase_tracking!, packages: [
      ...order.purchase_tracking!.packages!,
      { id: '2', order_numbers: ['second-order'], company: '顺丰', tracking_number: 'SF456', source: 'logistics', status: 'TRANSPORT', status_label: '运输中', updated_at: '', stale: true },
    ] } })
    expect(hint.sections.map(section => section.heading)).toEqual(['待报单', '1688 国内包裹 · 已签收', '1688 国内包裹 · 运输中'])
    expect(hint.sections[2]).toMatchObject({ lines: ['顺丰 · SF456', '采购单：second-order', '最后成功查询：未提供'], warning: '查询未更新，保留上次结果' })
    expect(hint.sections[1]!.lines.some(line => line.startsWith('最后成功查询：') && !line.endsWith('未提供'))).toBe(true)
  })
  it('1688 混合查询保留尚未查询和过期提示', () => {
    const hint = orderProgressHint({ ...order, purchase_tracking: { ...order.purchase_tracking!, has_waybill: false, unknown_count: 1, stale: true, packages: [] } })
    expect(hint.title).toBe('1688 采购与包裹状态')
    expect(JSON.stringify(hint)).toContain('保留上次结果')
    expect(JSON.stringify(hint)).toContain('1 条采购记录尚未查询')
  })
})
