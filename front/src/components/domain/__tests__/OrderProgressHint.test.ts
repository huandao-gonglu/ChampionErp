import { afterEach, expect, it, vi } from 'vitest'
import { DOMWrapper, enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import OrderProgressHint from '../OrderProgressHint.vue'
import { orderProgressHint } from '../orderProgressHint'
import type { OrderSnapshot } from '@/types/orders'

enableAutoUnmount(afterEach)
const packages = [
  { id: 'one', order_numbers: ['123'], company: '圆通', tracking_number: 'YT123', source: 'logistics' as const, status: 'SIGN', status_label: '已签收', updated_at: '2026-10-13T08:00:00Z', stale: true },
  { id: 'two', order_numbers: ['456'], company: '', tracking_number: '', source: 'order' as const, status: 'waitsellersend', status_label: '等待卖家发货', updated_at: '2026-10-13T09:00:00Z', stale: false },
]
it('悬浮与聚焦展示快照，Escape 关闭，不调用接口', async () => {
  const fetchSpy = vi.spyOn(window, 'fetch')
  try {
    const content = orderProgressHint({ platform: 'yandex', state: 'pending_shipment', fulfillment: 'FBS', procurement_status: 'purchased', purchase_tracking: { packages, orders: [], unknown_count: 0 } } as unknown as OrderSnapshot)
    const wrapper = mount(OrderProgressHint, { props: { content }, slots: { default: '<a href="#">部分待发货</a>' }, attachTo: document.body })
    expect(document.querySelector('[role="tooltip"]')).toBeNull()
    await wrapper.get('.progress-hint-anchor').trigger('mouseenter'); await flushPromises()
    const hint = new DOMWrapper(document.body).get('[role="tooltip"]')
    for (const text of ['1688 采购与包裹状态', '已签收', '等待卖家发货', 'YT123', '尚无运单', '保留上次结果', '最后成功查询']) expect(hint.text()).toContain(text)
    expect(hint.findAll('article')).toHaveLength(2)
    expect(fetchSpy).not.toHaveBeenCalled()
    await wrapper.get('a').trigger('keydown', { key: 'Escape' })
    expect(document.querySelector('[role="tooltip"]')).toBeNull()
    await wrapper.get('a').trigger('focusin'); await flushPromises()
    expect(document.querySelector('[role="tooltip"]')).not.toBeNull()
    window.dispatchEvent(new Event('resize'))
    await flushPromises()
    expect(document.querySelector('[role="tooltip"]')).toBeNull()
  } finally { fetchSpy.mockRestore() }
})
