import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import AlibabaPaymentAgreement from '../AlibabaPaymentAgreement.vue'
import { selfPurchasePaymentStatus, type PaymentAgreement } from '@/api/alibabaSelfPurchase'

vi.mock('@/api/alibabaSelfPurchase', () => ({ selfPurchasePaymentStatus: vi.fn() }))
afterEach(() => vi.resetAllMocks())

describe('免密签约渠道隔离', () => {
  it('切换到支付宝后忽略晚到的先采后付签约结果', async () => {
    let finish!: (value: PaymentAgreement) => void
    vi.mocked(selfPurchasePaymentStatus).mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
      .mockResolvedValueOnce({ channel: 'alipay', ready: false, signed: false, bound: true, sign_url: '', message: '支付宝尚未开通' })
    const wrapper = mount(AlibabaPaymentAgreement, { props: { channel: 'shegou' } })
    await wrapper.setProps({ channel: 'alipay' }); await flushPromises()
    finish({ channel: 'shegou', ready: true, signed: true, bound: true, sign_url: '', message: '先采后付已开通' })
    await flushPromises()
    expect(wrapper.text()).toContain('支付宝尚未开通')
    expect(wrapper.text()).not.toContain('先采后付已开通')
    expect(wrapper.emitted('ready')!.every(args => args[0] === false)).toBe(true)
    wrapper.unmount()
  })
  it('返回渠道不符时禁止支付', async () => {
    vi.mocked(selfPurchasePaymentStatus).mockResolvedValue({ channel: 'shegou', ready: true, signed: true, bound: true, sign_url: '', message: '已开通' })
    const wrapper = mount(AlibabaPaymentAgreement, { props: { channel: 'alipay' } })
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('渠道签约状态不一致')
    expect(wrapper.emitted('ready')!.every(args => args[0] === false)).toBe(true)
    wrapper.unmount()
  })
})
