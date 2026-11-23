import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import AlibabaDirectPurchaseView from '../AlibabaDirectPurchaseView.vue'
import * as api from '@/api/alibabaDirectPurchase'

vi.mock('@/api/alibabaDirectPurchase', () => ({
  fetchDirectPurchaseState: vi.fn(),
  fetchDirectPurchaseAddresses: vi.fn(),
  resolveDirectPurchaseProduct: vi.fn(),
  parseDirectPurchaseAddress: vi.fn(),
  previewDirectPurchase: vi.fn(),
  createDirectPurchase: vi.fn(),
  reconcileDirectPurchase: vi.fn(),
  fetchDirectPurchaseCashierUrl: vi.fn(),
  checkDirectPurchasePaymentStatus: vi.fn(),
  payDirectPurchase: vi.fn(),
}))

const basePreview = {
  offer_id: '7788990011',
  product_url: 'https://detail.1688.com/offer/7788990011.html',
  title: '日常办公收纳箱',
  supplier: '义乌日用百货厂',
  main_image: 'https://img.alicdn.com/main.jpg',
  items: [
    { offer_id: '7788990011', sku_id: '5001', spec_id: 'spec-1', specification: '颜色：白色', price: '25.00', image: '', quantity: 2 },
    { offer_id: '7788990011', sku_id: '5002', spec_id: 'spec-2', specification: '颜色：黑色', price: '30.00', image: '', quantity: 3 },
  ],
  total_quantity: 5,
  recipient: '张三',
  phone: '13800138000',
  address: '广东省 深圳市 南山区 科技园南路88号',
  goods_fen: 14000,
  shipping_fen: 800,
  total_fen: 14800,
  flow: 'general',
  pay_channels: ['alipay', 'shegou'],
  expires_at: Math.floor(Date.now() / 1000) + 600,
}

describe('AlibabaDirectPurchaseView', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    vi.mocked(api.fetchDirectPurchaseState).mockResolvedValue({
      ok: true,
      configured: true,
      authorized: true,
      session_ready: true,
      member_id: 'b2b-user-1',
      account_name: '自购买家',
      blocked_reason: '',
      records: [],
    })
    vi.mocked(api.fetchDirectPurchaseAddresses).mockResolvedValue({
      ok: true,
      items: [
        {
          id: '1688:addr-1',
          kind: '1688',
          label: '1688 保存地址',
          text: '张三\n13800138000\n广东省 深圳市 南山区 科技园南路88号',
          is_default: true,
          blocked_reason: '',
        },
      ],
      notice: '',
    })
    vi.mocked(api.resolveDirectPurchaseProduct).mockResolvedValue({
      ok: true,
      resolve_token: 'tok-1',
      offer_id: '7788990011',
      product_url: 'https://detail.1688.com/offer/7788990011.html',
      title: '日常办公收纳箱',
      supplier: '义乌日用百货厂',
      main_image: 'https://img.alicdn.com/main.jpg',
      skus: [
        { sku_id: '5001', spec_id: 'spec-1', specification: '颜色：白色', price: '25.00', stock: '100', image: '', purchasable: true, block_reason: '' },
        { sku_id: '5002', spec_id: 'spec-2', specification: '颜色：黑色', price: '30.00', stock: '50', image: '', purchasable: true, block_reason: '' },
        { sku_id: '5003', spec_id: '', specification: '颜色：缺标款', price: '18.00', stock: '5', image: '', purchasable: false, block_reason: '缺少 specId' },
      ],
    })
  })

  it('支持复选多个 SKU、选取 1688 地址下单、打开收银台并刷新同步额度', async () => {
    vi.mocked(api.previewDirectPurchase).mockResolvedValue({
      id: 'dp-1',
      state: 'preview',
      preview: basePreview,
      payment: null,
      order_numbers: [],
      order_status: '',
      order_status_code: '',
      message: '',
      pay_channel: '',
      quota_credited: false,
      created_at: '2026-11-23T07:00:00Z',
      updated_at: '2026-11-23T07:00:00Z',
    })
    vi.mocked(api.createDirectPurchase).mockResolvedValue({
      id: 'dp-1',
      state: 'created',
      preview: basePreview,
      payment: null,
      order_numbers: ['88001001'],
      order_status: '等待买家付款',
      order_status_code: 'waitbuyerpay',
      message: '1688 订单已创建（未自动扣款）。',
      pay_channel: 'alipay',
      quota_credited: false,
      created_at: '2026-11-23T07:00:01Z',
      updated_at: '2026-11-23T07:00:01Z',
    })
    vi.mocked(api.fetchDirectPurchaseCashierUrl).mockResolvedValue('https://cashier.1688.com/pay?order=88001001')
    vi.mocked(api.reconcileDirectPurchase).mockResolvedValue({
      id: 'dp-1',
      state: 'created',
      preview: basePreview,
      payment: null,
      order_numbers: ['88001001'],
      order_status: '等待卖家发货',
      order_status_code: 'waitsellersend',
      message: '已同步最新状态',
      pay_channel: 'alipay',
      quota_credited: true,
      created_at: '2026-11-23T07:00:01Z',
      updated_at: '2026-11-23T07:02:00Z',
    })

    const wrapper = mount(AlibabaDirectPurchaseView)
    await flushPromises()
    expect(wrapper.text()).toContain('万邦买家：自购买家')

    await wrapper.get('input[aria-label="1688 商品链接或分享文案"]').setValue('https://detail.1688.com/offer/7788990011.html')
    await wrapper.findAll('button').find((b) => b.text().includes('解析商品'))!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('日常办公收纳箱')
    expect(wrapper.get('input[aria-label="选择规格 颜色：缺标款"]').attributes('disabled')).toBeDefined()

    // 复选两个 SKU 并分别设置数量
    await wrapper.get('input[aria-label="选择规格 颜色：白色"]').setValue(true)
    await wrapper.get('input[aria-label="颜色：白色 购买数量"]').setValue('2')
    await wrapper.get('input[aria-label="选择规格 颜色：黑色"]').setValue(true)
    await wrapper.get('input[aria-label="颜色：黑色 购买数量"]').setValue('3')

    const previewBtn = wrapper.findAll('button').find((b) => b.text().includes('预览订单与运费'))!
    await previewBtn.trigger('click')
    await flushPromises()

    expect(api.previewDirectPurchase).toHaveBeenCalledWith({
      resolve_token: 'tok-1',
      items: [
        { sku_id: '5001', quantity: 2 },
        { sku_id: '5002', quantity: 3 },
      ],
      address_id: '1688:addr-1',
    })
    expect(wrapper.text()).toContain('¥148.00')

    await wrapper.get('input[aria-label="确认创建直购订单"]').setValue(true)
    await wrapper.findAll('button').find((b) => b.text().includes('确认创建 1688 订单'))!.trigger('click')
    await flushPromises()

    expect(api.createDirectPurchase).toHaveBeenCalledWith('dp-1', 'alipay')
    expect(wrapper.text()).toContain('88001001')

    await wrapper.findAll('button').find((b) => b.text().includes('获取 1688 收银台链接'))!.trigger('click')
    await flushPromises()
    expect(wrapper.find('a[href="https://cashier.1688.com/pay?order=88001001"]').exists()).toBe(true)

    await wrapper.findAll('button').find((b) => b.text() === '刷新状态')!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('等待卖家发货')
    expect(wrapper.text()).toContain('已按实付金额计入万邦调用额度')
  })

  it('支持切换手动输入地址（智能解析 + 手动修改并核对确认后预览）', async () => {
    vi.mocked(api.parseDirectPurchaseAddress).mockResolvedValue({
      ok: true,
      address: {
        fullName: '李四',
        mobile: '13900139000',
        phone: '',
        provinceText: '浙江省',
        cityText: '杭州市',
        areaText: '西湖区',
        townText: '',
        address: '文三路100号',
        postCode: '',
      },
      warnings: [],
    })
    vi.mocked(api.previewDirectPurchase).mockResolvedValue({
      id: 'dp-2',
      state: 'preview',
      preview: { ...basePreview, recipient: '李四', phone: '13900139000', address: '浙江省 杭州市 西湖区 文三路100号' },
      payment: null,
      order_numbers: [],
      order_status: '',
      order_status_code: '',
      message: '',
      pay_channel: '',
      quota_credited: false,
      created_at: '2026-11-23T07:05:00Z',
      updated_at: '2026-11-23T07:05:00Z',
    })

    const wrapper = mount(AlibabaDirectPurchaseView)
    await flushPromises()

    await wrapper.get('input[aria-label="1688 商品链接或分享文案"]').setValue('https://detail.1688.com/offer/7788990011.html')
    await wrapper.findAll('button').find((b) => b.text().includes('解析商品'))!.trigger('click')
    await flushPromises()
    await wrapper.get('input[aria-label="选择规格 颜色：白色"]').setValue(true)

    await wrapper.findAll('button').find((b) => b.text() === '手动输入地址')!.trigger('click')
    await wrapper.get('textarea[aria-label="粘贴完整地址"]').setValue('李四 13900139000 浙江省杭州市西湖区文三路100号')
    await wrapper.findAll('button').find((b) => b.text().includes('智能解析填充'))!.trigger('click')
    await flushPromises()

    // 未勾选确认前预览按钮禁用
    const previewBtn = wrapper.findAll('button').find((b) => b.text().includes('预览订单与运费'))!
    expect(previewBtn.attributes('disabled')).toBeDefined()

    await wrapper.get('input[aria-label="确认手动收货地址"]').setValue(true)
    expect(previewBtn.attributes('disabled')).toBeUndefined()

    await previewBtn.trigger('click')
    await flushPromises()

    expect(api.previewDirectPurchase).toHaveBeenCalledWith({
      resolve_token: 'tok-1',
      items: [{ sku_id: '5001', quantity: 1 }],
      address: {
        fullName: '李四',
        mobile: '13900139000',
        phone: '',
        provinceText: '浙江省',
        cityText: '杭州市',
        areaText: '西湖区',
        townText: '',
        address: '文三路100号',
        postCode: '',
      },
      address_confirmed: true,
    })
  })

  it('未授权万邦买家时提示拦截并支持跳转平台授权', async () => {
    vi.mocked(api.fetchDirectPurchaseState).mockResolvedValue({
      ok: true,
      configured: true,
      authorized: false,
      session_ready: false,
      member_id: '',
      account_name: '',
      blocked_reason: '请先在「平台授权 → 万邦 1688」完成个人 1688 买家自助授权',
      records: [],
    })

    const wrapper = mount(AlibabaDirectPurchaseView)
    await flushPromises()

    expect(wrapper.text()).toContain('当前暂不能使用 1688 万邦直购')
    await wrapper.findAll('button').find((b) => b.text().includes('去配置 / 授权万邦 1688'))!.trigger('click')
    expect(wrapper.emitted('openAuth')).toHaveLength(1)
  })

  it('图片使用 no-referrer 避免 1688 防盗链拦截，且切换 Tab 重新挂载后保留已拉取商品与勾选状态', async () => {
    const first = mount(AlibabaDirectPurchaseView)
    await flushPromises()
    expect(api.fetchDirectPurchaseAddresses).toHaveBeenCalledWith(false)

    await first.findAll('button').find((b) => b.text().includes('刷新 1688 地址'))!.trigger('click')
    await flushPromises()
    expect(api.fetchDirectPurchaseAddresses).toHaveBeenCalledWith(true)

    await first.get('input[aria-label="1688 商品链接或分享文案"]').setValue('https://detail.1688.com/offer/7788990011.html')
    await first.findAll('button').find((b) => b.text().includes('解析商品'))!.trigger('click')
    await flushPromises()

    const imgs = first.findAll('img')
    expect(imgs.length).toBeGreaterThan(0)
    expect(imgs.every((img) => img.attributes('referrerpolicy') === 'no-referrer')).toBe(true)

    await first.get('input[aria-label="选择规格 颜色：白色"]').setValue(true)
    await first.get('input[aria-label="颜色：白色 购买数量"]').setValue('4')
    first.unmount()

    // 模拟切走 Tab 后再回到 1688 直购
    const second = mount(AlibabaDirectPurchaseView)
    await flushPromises()
    expect(second.text()).toContain('日常办公收纳箱')
    expect((second.get('input[aria-label="选择规格 颜色：白色"]').element as HTMLInputElement).checked).toBe(true)
    expect((second.get('input[aria-label="颜色：白色 购买数量"]').element as HTMLInputElement).value).toBe('4')
  })
})
