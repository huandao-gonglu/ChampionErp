import { beforeEach, describe, expect, it, vi } from 'vitest'
import { apiClient } from '../client'
import { createSelfPurchase } from '../alibabaSelfPurchase'

vi.mock('../client', () => ({ apiClient: { post: vi.fn() } }))
const base = '/api/orders/alibaba-purchase/'
const record = { id: 'purchase', state: 'created', purchase_record_id: 'record', preview: { total_fen: 1580 }, payment: null }
beforeEach(() => vi.resetAllMocks())
describe.each(['shegou', 'alipay'])('%s 创建与支付编排', channel => {
  it('确认对应渠道签约后创建，使用返回的冻结金额支付', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({ data: { agreement: { channel, ready: true } } })
      .mockResolvedValueOnce({ data: { record: { ...record, pay_channel: channel } } })
      .mockResolvedValueOnce({ data: { record: { ...record, pay_channel: channel, payment: { state: 'succeeded' } } } })
    const value = await createSelfPurchase('purchase', channel)
    expect(value.payment?.state).toBe('succeeded')
    expect(apiClient.post).toHaveBeenNthCalledWith(1, base + 'payment-status', { pay_channel: channel }, expect.anything())
    expect(apiClient.post).toHaveBeenNthCalledWith(2, base + 'create', { preview_id: 'purchase', pay_channel: channel }, expect.anything())
    expect(apiClient.post).toHaveBeenNthCalledWith(3, base + 'pay', { preview_id: 'purchase', confirmed_total_fen: 1580 }, expect.anything())
  })
  it('未签约不创建订单', async () => {
    vi.mocked(apiClient.post).mockResolvedValue({ data: { agreement: { channel, ready: false, message: '请先签约' } } })
    await expect(createSelfPurchase('purchase', channel)).rejects.toThrow('请先签约')
    expect(apiClient.post).toHaveBeenCalledTimes(1)
  })
  it.each(['unknown', 'submitting', 'succeeded'])('已有 %s 支付回执不再次请求支付', async state => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({ data: { agreement: { channel, ready: true } } })
      .mockResolvedValueOnce({ data: { record: { ...record, pay_channel: channel, payment: { state } } } })
    await createSelfPurchase('purchase', channel)
    expect(apiClient.post).toHaveBeenCalledTimes(2)
  })
  it('支付网络超时不重试、不切换渠道', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({ data: { agreement: { channel, ready: true } } })
      .mockResolvedValueOnce({ data: { record: { ...record, pay_channel: channel } } }).mockRejectedValueOnce(new Error('超时'))
    await expect(createSelfPurchase('purchase', channel)).rejects.toThrow('超时')
    expect(apiClient.post).toHaveBeenCalledTimes(3)
  })
  it('回执渠道不符不能支付', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({ data: { agreement: { channel, ready: true } } })
      .mockResolvedValueOnce({ data: { record: { ...record, pay_channel: channel === 'alipay' ? 'shegou' : 'alipay' } } })
    await expect(createSelfPurchase('purchase', channel)).rejects.toThrow('渠道不一致')
    expect(apiClient.post).toHaveBeenCalledTimes(2)
  })
})
