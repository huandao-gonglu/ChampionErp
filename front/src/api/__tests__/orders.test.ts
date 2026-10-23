import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fetchOrders } from '../orders'
import { apiClient } from '../client'
vi.mock('../client', () => ({
  apiClient: { get: vi.fn().mockResolvedValue({ data: { ok: true } }) },
}))
describe('订单列表请求', () => {
  beforeEach(() => vi.clearAllMocks())
  it('按九条分页，在服务端应用搜索与平台状态筛选', async () => {
    await fetchOrders({ platform: 'yandex', state: 'pending_shipment', q: 'SKU 蓝色', offset: 9 })
    const url = new URL(vi.mocked(apiClient.get).mock.calls[0]![0], 'http://localhost')
    expect(Object.fromEntries(url.searchParams)).toEqual({
      platform: 'yandex',
      state: 'pending_shipment',
      q: 'SKU 蓝色',
      offset: '9',
      limit: '9',
    })
  })
  it('编码关联字段和组合筛选，日期查询携带时区并省略空条件', async () => {
    await fetchOrders({ platform: '', state: '', offset: 0, q: 'YT+123&备注', procurement_status: 'purchased',
      declaration_status: 'unsubmitted', has_waybill: 'yes', receiving_warehouse: '义乌 一仓',
      deadline_from: '2026-10-18', deadline_to: '2026-10-19', time_zone: 'Asia/Shanghai', domestic_carrier: '' })
    const url = new URL(vi.mocked(apiClient.get).mock.calls[0]![0], 'http://localhost')
    expect(Object.fromEntries(url.searchParams)).toEqual({ offset: '0', limit: '9', q: 'YT+123&备注',
      procurement_status: 'purchased', declaration_status: 'unsubmitted', has_waybill: 'yes', receiving_warehouse: '义乌 一仓',
      deadline_from: '2026-10-18', deadline_to: '2026-10-19', time_zone: 'Asia/Shanghai' })
  })
})
