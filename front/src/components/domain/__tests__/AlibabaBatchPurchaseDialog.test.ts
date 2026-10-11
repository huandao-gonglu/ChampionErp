import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { DOMWrapper, flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import AlibabaBatchPurchaseDialog from '../AlibabaBatchPurchaseDialog.vue'
import * as api from '@/api/alibabaSelfPurchase'
import type { BatchPurchaseOptions, SelfPurchaseRecord } from '@/api/alibabaSelfPurchase'

vi.mock('@/api/alibabaSelfPurchase', () => ({ batchPurchaseOptions: vi.fn(), previewBatchPurchase: vi.fn(), createSelfPurchase: vi.fn(), reconcileSelfPurchase: vi.fn(), selfPurchaseCashier: vi.fn(), selfPurchasePaymentStatus: vi.fn(), paySelfPurchase: vi.fn() }))
const candidate = { id: '1', offer_id: '123', sku_id: '1', specification: '蓝色', product_url: 'https://detail.1688.com/offer/123.html' }
function group(id = 'group-a', keys = ['line-1', 'line-2']): SelfPurchaseRecord {
  return { id, state: 'preview', order_numbers: [], order_status: '', message: '', pay_channel: '', purchase_record_id: '', created_at: '2026-10-10',
    preview: { candidate, quantity: keys.length * 2, recipient: '仓库', phone: '13800000000', address: '义乌收货地址', goods_fen: 2000, shipping_fen: 500, total_fen: 2500, flow: 'general', pay_channels: ['alipay'], expires_at: Date.now() / 1000 + 600,
      supplier: id, items: keys.map(key => ({ line_key: key, title: key, supplier: id, candidate, quantity: 2 })) } }
}
let current: BatchPurchaseOptions
let wrapper: VueWrapper | undefined
const screen = () => new DOMWrapper(document.body)
const button = (label: string) => screen().findAll('button').find(item => item.text() === label)!
beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(api.selfPurchasePaymentStatus).mockImplementation(async channel => ({ channel: channel as 'alipay' | 'shegou', ready: true, signed: true, bound: true, sign_url: '', message: '已开通免密支付' }))
  current = { ok: true, records: [], lines: [1, 2, 3].map(i => ({ line_key: `line-${i}`, title: `商品${i}`, sku: `SKU-${i}`, supplier: i === 3 ? 'B商家' : 'A商家', options: {
    ok: true, warehouse_required: true, receiving_revision: 7, warehouse_name: '义乌合作仓',
    receiving_address: { fullName: '仓库', mobile: '13800000000', phone: '', provinceText: '浙江省', cityText: '金华市', areaText: '义乌市', townText: '', address: '收货地址', postCode: '' },
    remaining_quantity: 2, can_purchase: true, blocked_reason: '', candidates: [{ ...candidate, sku_id: String(i) }], records: [],
  } })) }
  vi.mocked(api.batchPurchaseOptions).mockImplementation(async () => structuredClone(current))
  vi.mocked(api.previewBatchPurchase).mockResolvedValue([group(), group('group-b', ['line-3'])])
  vi.mocked(api.createSelfPurchase).mockImplementation(async (id, channel) => {
    const value = { ...(id === 'group-a' ? group() : group('group-b', ['line-3'])), state: 'created', pay_channel: channel, payment: { state: 'succeeded', channel, amount_fen: 2500, request_id: id, message: '支付确认成功' }, purchase_record_id: id, order_numbers: [id === 'group-a' ? '12345' : '67890'] }
    current.records.push(value)
    current.lines.forEach(line => { if (value.preview.items!.some(item => item.line_key === line.line_key)) { line.options.remaining_quantity = 0; line.options.can_purchase = false } })
    return value
  })
})
afterEach(() => { wrapper?.unmount(); wrapper = undefined; document.body.innerHTML = '' })
async function render() {
  wrapper = mount(AlibabaBatchPurchaseDialog, { attachTo: document.body, props: { orderId: 'order' } })
  await flushPromises()
}
async function prepare() {
  await button('全选可采购商品').trigger('click')
  await button('按供应商预览').trigger('click')
  await flushPromises()
}
async function submit() {
  await screen().get('[aria-label="确认批量采购"]').setValue(true)
  await button('确认批量创建并支付').trigger('click')
  await flushPromises()
}

describe('订单批量采购', () => {
  it.each(['shegou', 'alipay'])('%s 首组支付结果未知时暂停后续组', async channel => {
    const a = group(), b = group('group-b', ['line-3'])
    a.preview.pay_channels = b.preview.pay_channels = [channel]
    vi.mocked(api.previewBatchPurchase).mockResolvedValue([a, b])
    vi.mocked(api.createSelfPurchase).mockImplementation(async () => {
      const saved = { ...a, state: 'created', pay_channel: channel, purchase_record_id: 'record',
        payment: { state: 'unknown', channel, amount_fen: 2500, request_id: 'pay-one', message: '支付结果待核验' } }
      current.records = [saved]
      return saved
    })
    await render(); await prepare()
    expect(button('确认批量创建并支付').attributes('disabled')).toBeDefined()
    await screen().get('[aria-label="确认批量采购"]').setValue(true)
    await button('确认批量创建并支付').trigger('click'); await flushPromises()
    expect(api.createSelfPurchase).toHaveBeenCalledTimes(1)
    expect(api.createSelfPurchase).toHaveBeenCalledWith('group-a', channel)
    expect(screen().text()).toContain('已暂停后续分组')
    expect(screen().text()).toContain('支付结果待核验')
  })
  it('混合渠道必须分别开通，仅将支付宝分组计入直接扣款金额', async () => {
    const a = group(), b = group('group-b', ['line-3'])
    b.preview.pay_channels = ['shegou']
    vi.mocked(api.previewBatchPurchase).mockResolvedValue([a, b])
    vi.mocked(api.selfPurchasePaymentStatus).mockImplementation(async channel => ({ channel: channel as 'alipay' | 'shegou', ready: channel === 'shegou', signed: channel === 'shegou', bound: true, sign_url: '', message: channel === 'alipay' ? '支付宝尚未签约' : '先采后付已签约' }))
    await render(); await prepare()
    await screen().get('[aria-label="确认批量采购"]').setValue(true)
    expect(button('确认批量创建并支付').attributes('disabled')).toBeDefined()
    expect(screen().text()).toContain('支付宝免密将直接扣款 ¥25.00')
    expect(api.createSelfPurchase).not.toHaveBeenCalled()
    vi.mocked(api.selfPurchasePaymentStatus).mockResolvedValue({ channel: 'alipay', ready: true, signed: true, bound: true, sign_url: '', message: '支付宝已签约' })
    await screen().get('[aria-label="支付宝免密签约"] button').trigger('click'); await flushPromises()
    expect(button('确认批量创建并支付').attributes('disabled')).toBeUndefined()
    await submit()
    expect(api.createSelfPurchase).toHaveBeenNthCalledWith(1, 'group-a', 'alipay')
    expect(api.createSelfPurchase).toHaveBeenNthCalledWith(2, 'group-b', 'shegou')
  })
  it('勾选多 SKU 并使用冻结仓库版本，按分组分别提交及展示采购单号', async () => {
    await render(); await prepare()
    expect(api.previewBatchPurchase).toHaveBeenCalledWith({ order_id: 'order', receiving_revision: 7, items: [1, 2, 3].map(i => ({ line_key: `line-${i}`, candidate_id: '1', quantity: 2 })) })
    expect(screen().text()).toContain('义乌合作仓')
    expect(screen().text()).toContain('本次 2 个采购分组')
    expect(button('确认批量创建并支付').attributes('disabled')).toBeDefined()
    await submit()
    expect(api.createSelfPurchase).toHaveBeenNthCalledWith(1, 'group-a', 'alipay')
    expect(api.createSelfPurchase).toHaveBeenNthCalledWith(2, 'group-b', 'alipay')
    expect(screen().text()).toContain('采购单号：12345')
    expect(screen().text()).toContain('采购单号：67890')
    expect(wrapper!.emitted('updated')).toHaveLength(2)
  })
  it('修改数量使整批旧预览失效，缺少来源的 SKU 不能勾选', async () => {
    current.lines[2]!.options.can_purchase = false
    current.lines[2]!.options.blocked_reason = '请先确认来源'
    await render(); await prepare()
    expect(screen().get('[aria-label="采购 SKU-3"]').attributes('disabled')).toBeDefined()
    await screen().get('[aria-label="SKU-1 采购数量"]').setValue(1)
    expect(button('确认批量创建并支付')).toBeUndefined()
    expect(api.createSelfPurchase).not.toHaveBeenCalled()
  })
  it('部分失败保留成功分组，重新预览只选择剩余商品', async () => {
    vi.mocked(api.createSelfPurchase).mockImplementation(async id => {
      const value = { ...(id === 'group-a' ? group() : group('group-b', ['line-3'])), state: id === 'group-a' ? 'created' : 'failed', payment: id === 'group-a' ? { state: 'succeeded', channel: 'alipay', amount_fen: 2500, request_id: id, message: '支付宝支付确认成功' } : null, purchase_record_id: id === 'group-a' ? id : '' }
      current.records.push(value)
      current.lines.slice(0, 2).forEach(line => { line.options.can_purchase = false; line.options.remaining_quantity = 0 })
      return value
    })
    await render(); await prepare(); await submit()
    await button('全选可采购商品').trigger('click')
    await button('按供应商预览').trigger('click'); await flushPromises()
    expect(api.previewBatchPurchase).toHaveBeenLastCalledWith(expect.objectContaining({ items: [{ line_key: 'line-3', candidate_id: '1', quantity: 2 }] }))
  })
  it('提交超时暂停后续分组；重新打开仍能查询原请求且不重发', async () => {
    vi.mocked(api.createSelfPurchase).mockImplementation(async () => {
      current.records = [{ ...group(), state: 'unknown' }]
      throw new Error('请求超时')
    })
    await render(); await prepare(); await submit()
    expect(api.createSelfPurchase).toHaveBeenCalledTimes(1)
    expect(screen().text()).toContain('已暂停后续分组')
    wrapper!.unmount(); wrapper = undefined
    await render()
    expect(screen().text()).toContain('结果待核验')
    expect(screen().get('[aria-label="采购 SKU-1"]').attributes('disabled')).toBeDefined()
    vi.mocked(api.reconcileSelfPurchase).mockResolvedValue({ ...group(), state: 'created', purchase_record_id: 'record' })
    await button('查询原订单').trigger('click'); await flushPromises()
    expect(api.reconcileSelfPurchase).toHaveBeenCalledWith('group-a')
    expect(api.createSelfPurchase).toHaveBeenCalledTimes(1)
  })
  it('提交期间禁止关闭；内容区按下后移到遮罩松开不关闭，正常遮罩点击关闭', async () => {
    await render()
    const dialog = screen().get('dialog')
    await screen().get('h4').trigger('pointerdown', { pointerId: 1, button: 0, isPrimary: true })
    await dialog.trigger('pointerup', { pointerId: 1, button: 0, isPrimary: true })
    expect(wrapper!.emitted('close')).toBeUndefined()
    await dialog.trigger('pointerdown', { pointerId: 1, button: 0, isPrimary: true })
    await dialog.trigger('pointerup', { pointerId: 1, button: 0, isPrimary: true })
    expect(wrapper!.emitted('close')).toHaveLength(1)
    await prepare()
    let finish!: (record: SelfPurchaseRecord) => void
    vi.mocked(api.createSelfPurchase).mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    await screen().get('[aria-label="确认批量采购"]').setValue(true)
    await button('确认批量创建并支付').trigger('click')
    expect(screen().get('[aria-label="关闭1688 批量采购"]').attributes('disabled')).toBeDefined()
    finish({ ...group(), state: 'unknown' })
    await flushPromises()
    expect(screen().get('[aria-label="关闭1688 批量采购"]').attributes('disabled')).toBeUndefined()
  })
})
