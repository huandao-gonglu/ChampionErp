import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import AlibabaSelfPurchaseDialog from '../AlibabaSelfPurchaseDialog.vue'
import * as api from '@/api/alibabaSelfPurchase'
import type { SelfPurchaseOptions, SelfPurchaseRecord } from '@/api/alibabaSelfPurchase'

vi.mock('@/api/alibabaSelfPurchase', () => ({ selfPurchaseOptions: vi.fn(), previewSelfPurchase: vi.fn(), createSelfPurchase: vi.fn(), reconcileSelfPurchase: vi.fn(), selfPurchaseCashier: vi.fn() }))
const candidate = { id: 'source', offer_id: '123', sku_id: '456', specification: '蓝色', product_url: 'https://detail.1688.com/offer/123.html' }
function preview(): SelfPurchaseRecord {
  return { id: 'request-one', state: 'preview', order_numbers: [], order_status: '', message: '', pay_channel: '', purchase_record_id: '', created_at: '2026-10-08',
    preview: { candidate, quantity: 1, recipient: '测试用户', phone: '13800000000', address: '四川成都测试地址', goods_fen: 980, shipping_fen: 600, total_fen: 1580, flow: 'fenxiaonew', pay_channels: ['alipay', 'shegou'], expires_at: Date.now() / 1000 + 600 } }
}
let current: SelfPurchaseOptions
let wrapper: VueWrapper | undefined
beforeEach(() => {
  vi.resetAllMocks()
  current = { ok: true, remaining_quantity: 2, can_purchase: true, blocked_reason: '', candidates: [candidate], records: [] }
  vi.mocked(api.selfPurchaseOptions).mockImplementation(async () => structuredClone(current))
  vi.mocked(api.previewSelfPurchase).mockImplementation(async () => { current.records = [preview()]; return preview() })
  vi.mocked(api.createSelfPurchase).mockImplementation(async () => {
    const created = { ...preview(), state: 'created', pay_channel: 'shegou', order_numbers: ['3317081160226242182'], message: '订单已创建，尚未发起支付' }
    current.records = [created]; return created
  })
})
afterEach(() => { wrapper?.unmount(); wrapper = undefined; document.body.innerHTML = '' })
async function render() {
  wrapper = mount(AlibabaSelfPurchaseDialog, { attachTo: document.body, props: { orderId: 'sale-order', lineKey: 'line-one', title: '测试商品' }, global: { stubs: { Teleport: true } } })
  await flushPromises()
  return wrapper
}
function button(text: string) { return wrapper!.findAll('button').find(b => b.text() === text)! }
async function prepare() {
  await button('读取默认地址并预览').trigger('click'); await flushPromises()
}

describe('1688 采购', () => {
  it('缺少上架规格时弹出具体阻断原因，不允许预览或下单', async () => {
    current = { ...current, candidates: [], can_purchase: false, blocked_reason: '采集上架记录缺少下单规格标识（specId）' }
    await render()
    expect(wrapper!.get('[role="alert"]').text()).toContain('不能自动采购')
    expect(wrapper!.get('[role="alert"]').text()).toContain('specId')
    expect(button('读取默认地址并预览')).toBeUndefined()
    expect(button('确认创建订单')).toBeUndefined()
    expect(api.previewSelfPurchase).not.toHaveBeenCalled()
    expect(api.createSelfPurchase).not.toHaveBeenCalled()
  })
  it('展示默认地址和金额，明确选择渠道并确认后才创建', async () => {
    await render(); await prepare()
    expect(wrapper!.find('[aria-label="历史采购单号"]').exists()).toBe(false)
    expect(wrapper!.text()).toContain('¥15.80')
    expect(wrapper!.text()).toContain('四川成都测试地址')
    expect(api.createSelfPurchase).not.toHaveBeenCalled()
    expect(button('确认创建订单').attributes('disabled')).toBeDefined()
    await wrapper!.get('[aria-label="预选支付渠道"]').setValue('shegou')
    await wrapper!.get('[aria-label="确认真实采购"]').setValue(true)
    await button('确认创建订单').trigger('click'); await flushPromises()
    expect(api.previewSelfPurchase).toHaveBeenCalledWith({ order_id: 'sale-order', line_key: 'line-one', candidate_id: 'source', quantity: 2 })
    expect(wrapper!.emitted('updated')).toBeDefined()
    expect(api.createSelfPurchase).toHaveBeenCalledTimes(1)
    expect(api.createSelfPurchase).toHaveBeenCalledWith('request-one', 'shegou')
    expect(wrapper!.text()).toContain('3317081160226242182')
    expect(wrapper!.text()).toContain('尚未发起支付')
    expect(wrapper!.text()).not.toContain('支付成功')
    expect(button('确认创建订单')).toBeUndefined()
  })
  it('改动数量使旧预览失效，不能提交旧价格', async () => {
    await render(); await prepare()
    await wrapper!.get('[aria-label="采购数量"]').setValue(1)
    expect(button('确认创建订单')).toBeUndefined()
    expect(api.createSelfPurchase).not.toHaveBeenCalled()
  })
  it('提交 HTTP 超时后只能查询原请求，恢复记录后显示订单号', async () => {
    await render(); await prepare()
    vi.mocked(api.createSelfPurchase).mockRejectedValueOnce(new Error('请求超时'))
    await wrapper!.get('[aria-label="预选支付渠道"]').setValue('shegou')
    await wrapper!.get('[aria-label="确认真实采购"]').setValue(true)
    await button('确认创建订单').trigger('click'); await flushPromises()
    expect(button('确认创建订单')).toBeUndefined()
    expect(wrapper!.text()).toContain('勿重复下单')
    current.records = [{ ...preview(), state: 'created', order_numbers: ['3317081160226242182'] }]
    vi.mocked(api.reconcileSelfPurchase).mockResolvedValue(current.records[0]!)
    await button('查询原订单').trigger('click'); await flushPromises()
    expect(api.reconcileSelfPurchase).toHaveBeenCalledWith('request-one')
    expect(api.createSelfPurchase).toHaveBeenCalledTimes(1)
    expect(wrapper!.text()).toContain('3317081160226242182')
  })
  it('刷新页面恢复未知请求，不能另起一笔下单', async () => {
    current.records = [{ ...preview(), state: 'unknown', message: '创建结果待核验' }]
    await render()
    expect(wrapper!.get('fieldset').attributes('disabled')).toBeDefined()
    expect(button('确认创建订单')).toBeUndefined()
    expect(button('查询原订单')).toBeDefined()
  })
  it('仅在用户获取链接后显示收银台入口', async () => {
    current.records = [{ ...preview(), state: 'created', order_numbers: ['3317081160226242182'] }]
    vi.mocked(api.selfPurchaseCashier).mockResolvedValue('https://trade.1688.com/order/cashier.htm')
    await render()
    expect(api.selfPurchaseCashier).not.toHaveBeenCalled()
    await button('获取 1688 收银台链接').trigger('click'); await flushPromises()
    expect(wrapper!.get('a[href="https://trade.1688.com/order/cashier.htm"]').text()).toBe('打开 1688 收银台')
  })
  it('内部按下外部松开不关闭，正常遮罩点击关闭', async () => {
    await render()
    const dialog = wrapper!.get('dialog')
    await wrapper!.get('fieldset').trigger('pointerdown', { button: 0, pointerId: 1, isPrimary: true })
    await dialog.trigger('pointerup', { button: 0, pointerId: 1, isPrimary: true })
    expect(wrapper!.emitted('close')).toBeUndefined()
    await dialog.trigger('pointerdown', { button: 0, pointerId: 2, isPrimary: true })
    await dialog.trigger('pointerup', { button: 0, pointerId: 2, isPrimary: true })
    expect(wrapper!.emitted('close')).toHaveLength(1)
  })
})
