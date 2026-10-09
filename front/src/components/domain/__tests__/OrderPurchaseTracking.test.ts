import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { syncPurchase } from '@/api/orders'
import type { OrderDetail, PurchaseRecord } from '@/types/orders'
import type { BackendAlibabaPurchaseQueryResult } from '@/types/workflow.generated'
import OrderPurchaseTracking from '../OrderPurchaseTracking.vue'

vi.mock('@/api/orders', () => ({ syncPurchase: vi.fn() }))
enableAutoUnmount(afterEach)
const record: PurchaseRecord = {
  id: 'purchase-1', line_key: 'line-1', request_id: 'request-1', quantity: 1,
  purchase_order_number: '3317081160226242182', created_at: '', cancelled_at: '', status: 'purchased',
  source: { source_platform: '1688', supplier: '', product_url: 'https://detail.1688.com/offer/1.html', source_sku_id: '', specification: '蓝色', sku_url: '', sku_url_verified: false },
}
function response(): BackendAlibabaPurchaseQueryResult {
  return { ok: true, record_id: record.id, order_number: record.purchase_order_number,
    checked_at: '2026-10-08T04:00:00Z', order: { order_number: record.purchase_order_number, status: 'waitbuyerreceive', status_label: '等待买家收货' }, logistics: null, logistics_warning: '' }
}
function render(value = record) {
  return mount(OrderPurchaseTracking, { props: { orderId: 'sales-order-1', record: value } })
}
beforeEach(() => { vi.resetAllMocks() })

function persisted(): PurchaseRecord {
  const data = response()
  data.logistics = [
    { logistics_id: 'LP1', company: '圆通', tracking_number: 'YT001', status: 'SIGN', status_label: '已签收', steps: [{ time: '2026-10-08', description: '包裹已签收' }] },
    { logistics_id: 'LP2', company: '顺丰', tracking_number: 'SF002', status: 'TRANSPORT', status_label: '运输中', steps: [] },
  ]
  return { ...record, progress: { state: 'pending_assignment', attempted_at: '', message: '请确认包裹分配', error: '', data } }
}
describe('1688 采购进度', () => {
  it('读取持久快照，进入页面不重复请求；同一次刷新更新订单和物流', async () => {
    const wrapper = render(persisted())
    expect(syncPurchase).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('等待买家收货')
    expect(wrapper.text()).toContain('YT001')
    expect(wrapper.text()).toContain('SF002')
    expect(wrapper.text()).toContain('包裹已签收')
    const detail = { ok: true, lines: [], order: {} } as unknown as OrderDetail
    vi.mocked(syncPurchase).mockResolvedValue(detail)
    await wrapper.get('button').trigger('click'); await flushPromises()
    expect(syncPurchase).toHaveBeenCalledWith('sales-order-1', record.id, expect.any(AbortSignal))
    expect(wrapper.emitted('updated')?.[0]?.[0]).toEqual(detail)
    expect(wrapper.findAll('button')).toHaveLength(1)
  })

  it('失败保留快照并允许重试', async () => {
    vi.mocked(syncPurchase).mockRejectedValue(new Error('授权已过期'))
    const wrapper = render(persisted())
    await wrapper.get('button').trigger('click'); await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('授权已过期')
    expect(wrapper.text()).toContain('等待买家收货')
    expect(wrapper.get('button').attributes('disabled')).toBeUndefined()
  })

  it('切换记录后取消旧请求，忽略旧响应并释放锁', async () => {
    let resolve!: (value: OrderDetail) => void
    vi.mocked(syncPurchase).mockImplementation(() => new Promise(done => { resolve = done }))
    const wrapper = render()
    await wrapper.get('button').trigger('click')
    expect(wrapper.get('button').attributes('disabled')).toBeDefined()
    const signal = vi.mocked(syncPurchase).mock.calls[0]![2]!
    await wrapper.setProps({ orderId: 'another' })
    expect(signal.aborted).toBe(true)
    resolve({} as OrderDetail); await flushPromises()
    expect(wrapper.emitted('updated')).toBeUndefined()
    expect(wrapper.emitted('lock')?.at(-1)).toEqual([false])
  })

  it('隐藏作废和非 1688 采购入口', () => {
    expect(render({ ...record, status: 'cancelled' }).find('button').exists()).toBe(false)
    const other = { ...record, source: { ...record.source, source_platform: '其他', product_url: 'https://1688.com.example.test/p' } }
    expect(render(other).find('button').exists()).toBe(false)
  })
})

it('物流失败不隐藏订单状态，也不冒充成功但没有物流', () => {
  const data = response()
  data.logistics_warning = '物流查询失败：接口无权限'
  const wrapper = render({ ...record, progress: { state: 'synced', attempted_at: '', message: '采购订单状态已更新，物流本次未更新', error: '', data } })
  expect(wrapper.text()).toContain('等待买家收货')
  expect(wrapper.text()).toContain('物流查询失败：接口无权限')
  expect(wrapper.text()).not.toContain('本次刷新失败')
  expect(wrapper.text()).not.toContain('1688 暂未提供物流信息')
  expect(wrapper.text()).not.toContain('物流更新时间')
})

it('未发货的空物流显示无运单，保留查询成功的时间', () => {
  const data = { ...response(), logistics: [] }
  const wrapper = render({ ...record, progress: { state: 'synced', attempted_at: '', message: '', error: '', data } })
  expect(wrapper.text()).toContain('1688 暂未提供物流信息')
  expect(wrapper.text()).toContain('物流更新时间')
})


it('读取采购图片快照并可打开原图，刷新失败仍保留；作废记录只读展示', async () => {
  const value = persisted()
  value.progress!.data!.product = { offer_id: '1', sku_id: '22', spec_id: 'spec', name: '蓝色款', specification: '颜色：蓝色', image_url: 'https://cbu01.alicdn.com/sku.jpg' }
  const wrapper = render(value)
  expect(wrapper.get('img').attributes('src')).toBe('https://cbu01.alicdn.com/sku.jpg')
  expect(wrapper.get('a[aria-label="查看1688采购商品原图"]').attributes('href')).toBe('https://cbu01.alicdn.com/sku.jpg')
  expect(wrapper.text()).toContain('颜色：蓝色')
  expect(syncPurchase).not.toHaveBeenCalled()
  vi.mocked(syncPurchase).mockRejectedValue(new Error('请求失败'))
  await wrapper.get('button').trigger('click'); await flushPromises()
  expect(wrapper.find('img').exists()).toBe(true)
  await wrapper.setProps({ record: { ...value, status: 'cancelled' } })
  expect(wrapper.find('img').exists()).toBe(true)
  expect(wrapper.find('button').exists()).toBe(false)
})

it('图片匹配失败给出原因，旧快照无图片仍正常展示状态', () => {
  const value = persisted()
  value.progress!.data!.product = null
  value.progress!.data!.product_warning = '订单商品无法与采购 SKU 唯一匹配，暂不展示采购图片'
  const wrapper = render(value)
  expect(wrapper.find('img').exists()).toBe(false)
  expect(wrapper.text()).toContain('无法与采购 SKU 唯一匹配')
  expect(render(persisted()).text()).toContain('等待买家收货')
})
