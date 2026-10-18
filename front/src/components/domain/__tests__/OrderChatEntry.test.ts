import { afterEach, describe, expect, it, vi } from 'vitest'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { fetchOrderDetail } from '@/api/orders'
import { fetchOrderChatHistory } from '@/api/orderChat'
import OrderDetailPanel from '../OrderDetailPanel.vue'
import OrderChatDialog from '../OrderChatDialog.vue'
import type { OrderPlatform } from '@/types/orders'

vi.mock('@/api/orders', () => ({ fetchOrderDetail: vi.fn() }))
vi.mock('@/api/orderChat', () => ({ fetchOrderChatHistory: vi.fn(), sendOrderChatMessage: vi.fn() }))
enableAutoUnmount(afterEach)

describe('订单联系买家入口', () => {
  it.each<OrderPlatform>(['yandex', 'ozon'])('仅 Yandex 订单展示入口，打开不读取聊天：%s', async platform => {
    vi.clearAllMocks()
    vi.mocked(fetchOrderDetail).mockResolvedValue({ ok: true, lines: [], purchases: [], order: {
      id: `${platform}:4:FBS:123`, platform, account_id: '4', order_id: '123', fulfillment: 'FBS',
      status: 'PROCESSING', shipping_status: '', state: 'processing', title: '', amount: '', currency: '',
      updated_at: '', checked_at: '', items: [],
    } })
    const wrapper = mount(OrderDetailPanel, {
      props: { orderId: `${platform}:4:FBS:123` },
      global: { plugins: [createPinia()], stubs: {
        teleport: true, OrderHandoverPanel: true, OrderReceivingWarehouse: true, OrderFulfillmentPanel: true,
      } },
    })
    await flushPromises()
    const entry = wrapper.findAll('button').find(button => button.text() === '联系买家')
    expect(!!entry).toBe(platform === 'yandex')
    if (entry) {
      await entry.trigger('click')
      await flushPromises()
      expect(wrapper.findComponent(OrderChatDialog).props('orderId')).toBe('yandex:4:FBS:123')
      expect(wrapper.get('button[aria-label="关闭订单详情"]').attributes('disabled')).toBeDefined()
    }
    expect(fetchOrderChatHistory).not.toHaveBeenCalled()
  })
})
