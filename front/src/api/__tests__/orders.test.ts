import { describe, expect, it, vi } from 'vitest'
import { fetchOrders } from '../orders'
import { apiClient } from '../client'
vi.mock('../client', () => ({
  apiClient: { get: vi.fn().mockResolvedValue({ data: { ok: true } }) },
}))
describe('订单列表请求', () => {
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
})
