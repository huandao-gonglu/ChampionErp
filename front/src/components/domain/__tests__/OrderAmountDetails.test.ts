import { describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import OrderAmountDetails from '../OrderAmountDetails.vue'
import OrderNotificationsPanel from '../OrderNotificationsPanel.vue'
import { useOrderNotificationsStore } from '@/stores/orderNotifications'

vi.mock('@/api/orders', () => ({
  fetchOrderIntegrations: vi.fn().mockResolvedValue({ public_url: '', platforms: [] }),
  fetchOrders: vi.fn(),
  orderCommand: vi.fn(),
}))

describe('订单金额口径', () => {
  it('展示含补贴总额、金额组成及未扣费用说明', () => {
    const wrapper = mount(OrderAmountDetails, {
      props: {
        platform: 'yandex',
        value: {
          amount: '74.00',
          currency: 'CNY',
          amount_breakdown: { payment: '48.10', subsidy: '25.90', cashback: '0.00' },
        },
      },
    })
    expect(wrapper.text()).toContain('商品金额（含平台补贴）')
    expect(wrapper.text()).toContain('74.00 CNY')
    expect(wrapper.text()).toContain('付款 48.10 CNY')
    expect(wrapper.text()).toContain('平台补贴 25.90 CNY')
    expect(wrapper.text()).toContain('未扣除佣金、物流等费用')
    expect(wrapper.text()).not.toContain('积分抵扣')
  })

  it('未同步的历史金额只能标为付款，不能当作含补贴总额', () => {
    const wrapper = mount(OrderAmountDetails, {
      props: {
        platform: 'yandex',
        value: { amount: '48.1', currency: 'CNY' },
      },
    })
    expect(wrapper.text()).toContain('付款金额（待同步明细）')
    expect(wrapper.text()).not.toContain('商品金额（含平台补贴）')
  })

  it('零金额仍展示，缺失金额显示待确认，积分抵扣单列', async () => {
    const wrapper = mount(OrderAmountDetails, {
      props: {
        platform: 'yandex',
        value: {
          amount: '0.00',
          currency: 'CNY',
          amount_breakdown: { payment: '0.00', subsidy: '0.00', cashback: '0.00' },
        },
      },
    })
    expect(wrapper.text()).toContain('0.00 CNY')
    await wrapper.setProps({ value: { amount: '', currency: '' } })
    expect(wrapper.text()).toContain('金额待确认')
    await wrapper.setProps({
      value: {
        amount: '74.00',
        currency: 'CNY',
        amount_breakdown: { payment: '40.00', subsidy: '25.90', cashback: '8.10' },
      },
    })
    expect(wrapper.text()).toContain('积分抵扣 8.10 CNY')
  })

  it('订单展开后展示各 SKU 数量及小计', async () => {
    setActivePinia(createPinia())
    const store = useOrderNotificationsStore()
    store.page.items = [
      {
        id: 'yandex:4:FBS:62668010304',
        platform: 'yandex',
        account_id: '4',
        order_id: '62668010304',
        fulfillment: 'FBS',
        title: '商品',
        status: 'PROCESSING',
        shipping_status: 'READY_TO_SHIP',
        state: 'pending_shipment',
        updated_at: '',
        checked_at: '',
        amount: '74.00',
        currency: 'CNY',
        amount_breakdown: { payment: '48.10', subsidy: '25.90', cashback: '0.00' },
        items: ['sku-a', 'sku-b'].map((sku) => ({
          sku,
          title: '商品',
          quantity: 1,
          amount: '37.00',
          currency: 'CNY',
          amount_breakdown: { payment: '24.05', subsidy: '12.95', cashback: '0.00' },
        })),
      },
    ]
    const wrapper = mount(OrderNotificationsPanel)
    await flushPromises()
    expect(wrapper.text()).toContain('共 2 件')
    const details = wrapper.get('tbody details')
    expect(details.text()).toContain('SKU：sku-a · 数量 1')
    expect(details.text()).toContain('SKU：sku-b · 数量 1')
    expect(details.findAll('li')).toHaveLength(2)
    for (const item of details.findAll('li')) {
      expect(item.text()).toContain('37.00 CNY')
      expect(item.text()).toContain('该 SKU 全部数量的小计')
    }
    wrapper.unmount()
  })
})
