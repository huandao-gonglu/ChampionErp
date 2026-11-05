import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import zh from '@/i18n/locales/zh'
import AlibabaSettingsPanel from '../AlibabaSettingsPanel.vue'
import * as api from '@/api/alibabaAuthorization'
import { openAuthLink } from '@/api/workflow/settings'

vi.mock('@/api/alibabaAuthorization', () => ({ fetchAlibabaAuthorization: vi.fn(), startAlibabaAuthorization: vi.fn(), completeAlibabaAuthorization: vi.fn(), testAlibabaOrder: vi.fn(), testAlibabaCrossborderProduct: vi.fn() }))
vi.mock('@/api/workflow/settings', () => ({ openAuthLink: vi.fn() }))
enableAutoUnmount(afterEach)
const status = () => ({ ok: true, application_configured: true, can_authorize: true, authorized: true, member_id: '买家甲', expires_at: 0, refreshable: true, needs_reauthorization: false })
function panel(crossborder = false) { return mount(AlibabaSettingsPanel, { props: { crossborder }, global: { plugins: [createI18n({ legacy: false, locale: 'zh', messages: { zh } })] } }) }
beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.fetchAlibabaAuthorization).mockResolvedValue(status())
  vi.mocked(api.startAlibabaAuthorization).mockResolvedValue({ ok: true, url: 'https://auth.1688.com/auth/authorize.htm?state=opaque' })
  vi.mocked(api.completeAlibabaAuthorization).mockResolvedValue({ ...status(), member_id: '买家乙' })
  vi.mocked(openAuthLink).mockResolvedValue({ ok: true, error: '', message: '', errorCode: '', nextAction: '', raw: {} })
})
describe('1688 账号连接', () => {
  it('跨境应用单独连接买家并只读验证商品', async () => {
    vi.mocked(api.testAlibabaCrossborderProduct).mockResolvedValue({ sku_count: 2, spec_id_count: 1 })
    const wrapper = panel(true); await flushPromises()
    expect(api.fetchAlibabaAuthorization).toHaveBeenCalledWith(true)
    await wrapper.get('[data-testid="connect-1688-crossborder"]').trigger('click'); await flushPromises()
    expect(api.startAlibabaAuthorization).toHaveBeenCalledWith(true)
    await wrapper.get('input[inputmode="numeric"]').setValue('123')
    await wrapper.findAll('form').at(-1)!.trigger('submit'); await flushPromises()
    expect(api.testAlibabaCrossborderProduct).toHaveBeenCalledWith('123')
    expect(wrapper.text()).toContain('返回 2 个 SKU，其中 1 个包含 specId')
    expect(api.testAlibabaOrder).not.toHaveBeenCalled()
  })
  it('只展示账号状态，官方登录与长订单号查询不提交任何企业凭据', async () => {
    const wrapper = panel(); await flushPromises()
    expect(wrapper.text()).toContain('买家甲')
    expect(wrapper.text()).not.toContain('AppSecret')
    expect(wrapper.findAll('input[type="password"]')).toHaveLength(0)
    await wrapper.get('[data-testid="connect-1688"]').trigger('click'); await flushPromises()
    expect(api.startAlibabaAuthorization).toHaveBeenCalledWith(false)
    expect(openAuthLink).toHaveBeenCalledWith('https://auth.1688.com/auth/authorize.htm?state=opaque')
    await wrapper.get('input[inputmode="numeric"]').setValue('3317081160226242182')
    await wrapper.findAll('form').at(-1)!.trigger('submit'); await flushPromises()
    expect(api.testAlibabaOrder).toHaveBeenCalledWith('3317081160226242182')
  })
  it('应用未配置时禁止登录，但允许刷新状态', async () => {
    vi.mocked(api.fetchAlibabaAuthorization).mockResolvedValue({ ...status(), authorized: false, application_configured: false, can_authorize: false, member_id: '' })
    const wrapper = panel(); await flushPromises()
    expect(wrapper.get('[data-testid="connect-1688"]').attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('请联系管理员')
    expect(api.startAlibabaAuthorization).not.toHaveBeenCalled()
  })
  it('手动完成回调后清空回调地址，并展示返回的真实账号', async () => {
    const wrapper = panel(); await flushPromises()
    await wrapper.get('[data-testid="connect-1688"]').trigger('click'); await flushPromises()
    const url = 'https://callback.test?code=private&state=opaque'
    await wrapper.get('input[type="password"]').setValue(url)
    await wrapper.findAll('form')[0]!.trigger('submit'); await flushPromises()
    expect(api.completeAlibabaAuthorization).toHaveBeenCalledWith(url, false)
    expect(wrapper.text()).toContain('买家乙')
    expect(wrapper.html()).not.toContain(url)
  })
  it('授权失败保留当前账号，清空一次性回调内容', async () => {
    vi.mocked(api.completeAlibabaAuthorization).mockRejectedValue(new Error('授权请求已失效'))
    const wrapper = panel(); await flushPromises()
    await wrapper.get('[data-testid="connect-1688"]').trigger('click'); await flushPromises()
    await wrapper.get('input[type="password"]').setValue('https://callback.test?code=private&state=expired')
    await wrapper.findAll('form')[0]!.trigger('submit'); await flushPromises()
    expect(wrapper.text()).toContain('买家甲')
    expect(wrapper.get('[role="alert"]').text()).toContain('已失效')
    expect((wrapper.get('input[type="password"]').element as HTMLInputElement).value).toBe('')
  })
})
