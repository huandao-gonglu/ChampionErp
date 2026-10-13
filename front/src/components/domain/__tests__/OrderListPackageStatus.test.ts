import { afterEach, expect, it, vi } from 'vitest'
import { DOMWrapper, enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import OrderListTable from '../OrderListTable.vue'
import type { OrdersPage } from '@/types/orders'
vi.mock('vue-router', () => ({ useRoute: () => ({ query: { tab: 'orders' } }) }))
enableAutoUnmount(afterEach)
it('列表显示最新的部分状态，悬浮保留全部包裹和可访问关联', async () => {
  const packages = [
    { id: 'old', order_numbers: ['123'], tracking_number: 'YT1', company: '圆通', source: 'logistics', status: 'SIGN', status_label: '已签收', updated_at: '2026-10-13T08:00:00Z', stale: false },
    { id: 'new', order_numbers: ['456'], tracking_number: 'SF2', company: '顺丰', source: 'logistics', status: 'TRANSPORT', status_label: '运输中', updated_at: '2026-10-13T09:00:00Z', stale: false },
  ]
  const page = { total: 1, items: [{ id: 'order', order_id: '1', platform: 'yandex', state: 'pending_shipment', fulfillment: 'FBS', status: 'PROCESSING', shipping_status: '', procurement_status: 'purchased', amount: '1', currency: 'RUB', items: [], purchase_tracking: { packages, orders: [], unknown_count: 0, has_waybill: true, stale: false } }] } as unknown as OrdersPage
  const wrapper = mount(OrderListTable, { props: { page, offset: 0, now: Date.now(), error: '' }, attachTo: document.body,
    global: { stubs: { RouterLink: { template: '<a><slot /></a>' } } } })
  const badge = wrapper.get('.order-progress-link')
  expect(badge.text()).toBe('部分运输中')
  await wrapper.get('.progress-hint-anchor').trigger('mouseenter'); await flushPromises()
  const hint = new DOMWrapper(document.body).get('[role="tooltip"]')
  expect(hint.text()).toContain('已签收')
  expect(hint.text()).toContain('运输中')
  expect(badge.attributes('aria-describedby')).toBe(hint.attributes('id'))
  // 同一行同步到仓库阶段后，已打开的 hint 也必须立即切换来源。
  await wrapper.setProps({ page: { ...page, items: [{ ...page.items[0]!, fulfillment_summary: {
    fulfillment_status: 'PACKING', crossborderbus_order_id: 99, busy: false, operation: '',
    create_unknown: false, cancel_requested: false, cancel_rejected: false, error_message: '', label_error: '',
    warehouse_name: '义乌仓', last_synced_at: '2026-10-13T10:00:00Z',
  } }] } })
  expect(badge.text()).toBe('已打包')
  const updatedHint = new DOMWrapper(document.body).get('[role="tooltip"]')
  expect(updatedHint.text()).toContain('跨境巴士履约状态')
  expect(updatedHint.text()).toContain('义乌仓')
  expect(updatedHint.text()).not.toContain('已签收')
  expect(updatedHint.text()).not.toContain('采购单')
  expect(badge.attributes('title')).toBeUndefined()
  expect(badge.attributes('aria-describedby')).toBe(updatedHint.attributes('id'))
})
