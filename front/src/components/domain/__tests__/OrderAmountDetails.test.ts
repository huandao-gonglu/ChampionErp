import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import OrderAmountDetails from '../OrderAmountDetails.vue'

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

})
