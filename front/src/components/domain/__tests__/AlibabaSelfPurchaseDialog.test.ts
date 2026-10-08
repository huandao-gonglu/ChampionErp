import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, DOMWrapper, type VueWrapper } from '@vue/test-utils'
import AlibabaSelfPurchaseDialog from '../AlibabaSelfPurchaseDialog.vue'
import * as api from '@/api/alibabaSelfPurchase'
import type { SelfPurchaseOptions, SelfPurchaseRecord } from '@/api/alibabaSelfPurchase'

vi.mock('@/api/alibabaSelfPurchase', () => ({ selfPurchaseOptions: vi.fn(), selfPurchaseAddresses: vi.fn(), parsePurchaseAddress: vi.fn(), previewSelfPurchase: vi.fn(), createSelfPurchase: vi.fn(), reconcileSelfPurchase: vi.fn(), selfPurchaseCashier: vi.fn() }))
const candidate = { id: 'source', offer_id: '123', sku_id: '456', specification: '蓝色', product_url: 'https://detail.1688.com/offer/123.html' }
function preview(): SelfPurchaseRecord {
  return { id: 'request-one', state: 'preview', order_numbers: [], order_status: '', message: '', pay_channel: '', purchase_record_id: '', created_at: '2026-10-08',
    preview: { candidate, quantity: 1, recipient: '测试用户', phone: '13800000000', address: '四川成都测试地址', goods_fen: 980, shipping_fen: 600, total_fen: 1580, flow: 'fenxiaonew', pay_channels: ['alipay', 'shegou'], expires_at: Date.now() / 1000 + 600 } }
}
let current: SelfPurchaseOptions
let wrapper: VueWrapper | undefined
beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(api.selfPurchaseAddresses).mockResolvedValue({ ok: true, notice: '', items: [
    { id: 'address-one', kind: '1688', label: '1688 保存地址', text: '测试用户 13800000000 四川成都测试地址', is_default: true, blocked_reason: '' },
    { id: 'address-two', kind: '1688', label: '1688 保存地址', text: '另一个收货人 广东深圳地址', is_default: false, blocked_reason: '' },
    { id: 'note-one', kind: 'note', label: '地址备注', text: '收件人：张三 电话：13800138000 地址：广东省深圳市南山区科技路1号', is_default: false, blocked_reason: '' },
  ] })
  current = { ok: true, remaining_quantity: 2, can_purchase: true, blocked_reason: '', candidates: [candidate], records: [] }
  vi.mocked(api.selfPurchaseOptions).mockImplementation(async () => structuredClone(current))
  vi.mocked(api.previewSelfPurchase).mockImplementation(async () => preview())
  vi.mocked(api.createSelfPurchase).mockImplementation(async () => {
    const created = { ...preview(), state: 'created', pay_channel: 'shegou', order_numbers: ['3317081160226242182'], message: '订单已创建，尚未发起支付' }
    current.records = [created]; return created
  })
})
afterEach(() => { wrapper?.unmount(); wrapper = undefined; document.body.innerHTML = '' })
async function render() {
  // 使用真实 Teleport，覆盖嵌套地址组件的挂载与弹窗关闭行为。
  wrapper = mount(AlibabaSelfPurchaseDialog, { attachTo: document.body, props: { orderId: 'sale-order', lineKey: 'line-one', title: '测试商品' } })
  await flushPromises()
  return wrapper
}
function screen() { return new DOMWrapper(document.body) }
function button(text: string) { return screen().findAll('button').find(b => b.text() === text)! }
async function prepare() {
  await button('预览订单').trigger('click'); await flushPromises()
}

describe('1688 采购', () => {
  it('切换非默认地址清除旧预览，支付方式独立于地址区域', async () => {
    await render(); await prepare()
    expect(api.selfPurchaseAddresses).toHaveBeenCalledTimes(1)
    expect(screen().get('[aria-label="收货地址"]').find('[aria-label="预选支付渠道"]').exists()).toBe(false)
    expect(screen().find('[aria-label="支付方式"]').exists()).toBe(true)
    await screen().get('[aria-label="选择收货地址"]').setValue('address-two')
    expect(button('确认创建订单')).toBeUndefined()
    await prepare()
    expect(api.previewSelfPurchase).toHaveBeenLastCalledWith(expect.objectContaining({ address_id: 'address-two' }))
  })
  it('地址备注解析后必须核对确认，修改字段后重新确认', async () => {
    vi.mocked(api.parsePurchaseAddress).mockResolvedValue({ ok: true, warnings: [], address: {
      fullName: '张三', mobile: '13800138000', phone: '', provinceText: '广东省', cityText: '深圳市', areaText: '南山区', townText: '', address: '科技路1号', postCode: '',
    } })
    await render()
    await screen().get('[aria-label="选择收货地址"]').setValue('note-one')
    expect((screen().get('[aria-label="粘贴完整地址"]').element as HTMLTextAreaElement).value).toContain('收件人：张三')
    expect(button('预览订单').attributes('disabled')).toBeDefined()
    await button('解析地址').trigger('click'); await flushPromises()
    expect((screen().get('[aria-label="省"]').element as HTMLInputElement).value).toBe('广东省')
    expect(button('预览订单').attributes('disabled')).toBeDefined()
    await screen().get('[aria-label="确认收货地址"]').setValue(true)
    await screen().get('[aria-label="详细地址"]').setValue('科技路2号')
    expect(button('预览订单').attributes('disabled')).toBeDefined()
    await screen().get('[aria-label="确认收货地址"]').setValue(true)
    await prepare()
    expect(api.previewSelfPurchase).toHaveBeenLastCalledWith(expect.objectContaining({ address_confirmed: true, address: expect.objectContaining({ address: '科技路2号' }) }))
    await screen().get('[aria-label="粘贴完整地址"]').setValue('另一个地址')
    expect(button('确认创建订单')).toBeUndefined()
    expect(button('预览订单').attributes('disabled')).toBeDefined()
  })
  it('手动输入信息缺失时不能确认地址', async () => {
    vi.mocked(api.parsePurchaseAddress).mockResolvedValue({ ok: true, warnings: ['未识别：省、市、电话，请手动补全'], address: {
      fullName: '', mobile: '', phone: '', provinceText: '', cityText: '', areaText: '', townText: '', address: '测试路1号', postCode: '',
    } })
    await render()
    await screen().get('[aria-label="选择收货地址"]').setValue('manual')
    await screen().get('[aria-label="粘贴完整地址"]').setValue('测试路1号')
    await button('解析地址').trigger('click'); await flushPromises()
    expect(screen().text()).toContain('请手动补全')
    expect(screen().get('[aria-label="确认收货地址"]').attributes('disabled')).toBeDefined()
    expect(button('预览订单').attributes('disabled')).toBeDefined()
  })
  it('预览仅在本次弹窗展示，重新打开不恢复，也不展示第二份采购历史', async () => {
    await render(); await prepare()
    expect(screen().text()).toContain('¥15.80')
    expect(screen().text()).not.toContain('采购请求号')
    expect(screen().find('details').exists()).toBe(false)
    wrapper!.unmount(); wrapper = undefined
    await render()
    expect(screen().find('[aria-label="采购结果"]').exists()).toBe(false)
    expect(button('确认创建订单')).toBeUndefined()
  })
  it('缺少上架规格时弹出具体阻断原因，不允许预览或下单', async () => {
    current = { ...current, candidates: [], can_purchase: false, blocked_reason: '采集上架记录缺少下单规格标识（specId）' }
    await render()
    expect(screen().get('[role="alert"]').text()).toContain('不能自动采购')
    expect(screen().get('[role="alert"]').text()).toContain('specId')
    expect(button('预览订单')).toBeUndefined()
    expect(button('确认创建订单')).toBeUndefined()
    expect(api.previewSelfPurchase).not.toHaveBeenCalled()
    expect(api.createSelfPurchase).not.toHaveBeenCalled()
  })
  it('展示默认地址和金额，明确选择渠道并确认后才创建', async () => {
    await render(); await prepare()
    expect(screen().find('[aria-label="历史采购单号"]').exists()).toBe(false)
    expect(screen().text()).toContain('¥15.80')
    expect(screen().text()).toContain('四川成都测试地址')
    expect(api.createSelfPurchase).not.toHaveBeenCalled()
    expect(button('确认创建订单').attributes('disabled')).toBeDefined()
    await screen().get('[aria-label="预选支付渠道"]').setValue('shegou')
    await screen().get('[aria-label="确认真实采购"]').setValue(true)
    await button('确认创建订单').trigger('click'); await flushPromises()
    expect(api.previewSelfPurchase).toHaveBeenCalledWith({ order_id: 'sale-order', line_key: 'line-one', candidate_id: 'source', quantity: 2, address_id: 'address-one' })
    expect(wrapper!.emitted('updated')).toBeDefined()
    expect(api.createSelfPurchase).toHaveBeenCalledTimes(1)
    expect(api.createSelfPurchase).toHaveBeenCalledWith('request-one', 'shegou')
    expect(screen().text()).toContain('3317081160226242182')
    expect(screen().text()).toContain('尚未发起支付')
    expect(screen().text()).not.toContain('支付成功')
    expect(button('确认创建订单')).toBeUndefined()
  })
  it('改动数量使旧预览失效，不能提交旧价格', async () => {
    await render(); await prepare()
    await screen().get('[aria-label="采购数量"]').setValue(1)
    expect(button('确认创建订单')).toBeUndefined()
    expect(api.createSelfPurchase).not.toHaveBeenCalled()
  })
  it('提交 HTTP 超时后只能查询原请求，恢复记录后显示订单号', async () => {
    await render(); await prepare()
    vi.mocked(api.createSelfPurchase).mockRejectedValueOnce(new Error('请求超时'))
    await screen().get('[aria-label="预选支付渠道"]').setValue('shegou')
    await screen().get('[aria-label="确认真实采购"]').setValue(true)
    await button('确认创建订单').trigger('click'); await flushPromises()
    expect(button('确认创建订单')).toBeUndefined()
    expect(screen().text()).toContain('勿重复下单')
    current.records = [{ ...preview(), state: 'created', order_numbers: ['3317081160226242182'] }]
    vi.mocked(api.reconcileSelfPurchase).mockResolvedValue(current.records[0]!)
    await button('查询原订单').trigger('click'); await flushPromises()
    expect(api.reconcileSelfPurchase).toHaveBeenCalledWith('request-one')
    expect(api.createSelfPurchase).toHaveBeenCalledTimes(1)
    expect(screen().text()).toContain('3317081160226242182')
  })
  it('刷新页面恢复未知请求，不能另起一笔下单', async () => {
    current.records = [{ ...preview(), state: 'unknown', message: '创建结果待核验' }]
    await render()
    expect(screen().get('fieldset').attributes('disabled')).toBeDefined()
    expect(button('确认创建订单')).toBeUndefined()
    expect(button('查询原订单')).toBeDefined()
  })
  it('仅在用户获取链接后显示收银台入口', async () => {
    current.records = [{ ...preview(), state: 'created', order_numbers: ['3317081160226242182'] }]
    vi.mocked(api.selfPurchaseCashier).mockResolvedValue('https://trade.1688.com/order/cashier.htm')
    await render()
    expect(api.selfPurchaseCashier).not.toHaveBeenCalled()
    await button('获取 1688 收银台链接').trigger('click'); await flushPromises()
    expect(screen().get('a[href="https://trade.1688.com/order/cashier.htm"]').text()).toBe('打开 1688 收银台')
  })
  it('内部按下外部松开不关闭，正常遮罩点击关闭', async () => {
    await render()
    const dialog = screen().get('dialog')
    await screen().get('fieldset').trigger('pointerdown', { button: 0, pointerId: 1, isPrimary: true })
    await dialog.trigger('pointerup', { button: 0, pointerId: 1, isPrimary: true })
    expect(wrapper!.emitted('close')).toBeUndefined()
    await dialog.trigger('pointerdown', { button: 0, pointerId: 2, isPrimary: true })
    await dialog.trigger('pointerup', { button: 0, pointerId: 2, isPrimary: true })
    expect(wrapper!.emitted('close')).toHaveLength(1)
  })
})
