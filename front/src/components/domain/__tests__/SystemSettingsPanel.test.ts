import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { reactive } from 'vue'
import { routeLocationKey } from 'vue-router'
import { useAppStore } from '@/stores/app'
import { useAccountStore } from '@/stores/account'
import SystemSettingsPanel from '../SystemSettingsPanel.vue'
import AppSidebar from '@/components/layout/AppSidebar.vue'
import { fetchSystemSettings, saveSystemSettings } from '@/api/systemSettings'
import { fetchUserAgents } from '@/api/userAgents'
vi.mock('@/api/userAgents', () => ({ fetchUserAgents: vi.fn(), saveUserAgents: vi.fn() }))
import { fetchYandexWarehouses } from '@/api/yandexWarehouses'
import { fetchForbiddenCategories } from '@/api/forbiddenCategories'
import { fetchManagedSettings } from '@/api/managedSettings'
import { fetchOneBoundQuota } from '@/api/alibabaAuthorization'
import { loadImageHosting } from '@/api/imageHosting'
vi.mock('@/api/forbiddenCategories', () => ({ fetchForbiddenCategories: vi.fn(), importForbiddenCategories: vi.fn() }))
vi.mock('@/api/yandexWarehouses', () => ({ fetchYandexWarehouses: vi.fn(), saveYandexWarehouses: vi.fn() }))
vi.mock('@/api/systemSettings', () => ({ fetchSystemSettings: vi.fn(), saveSystemSettings: vi.fn() }))
vi.mock('@/api/managedSettings', () => ({ fetchManagedSettings: vi.fn(), saveManagedSettings: vi.fn() }))
vi.mock('@/api/alibabaAuthorization', () => ({ fetchOneBoundQuota: vi.fn(), restoreOneBoundKey: vi.fn() }))
vi.mock('@/api/imageHosting', () => ({
  loadImageHosting: vi.fn(), saveImageHosting: vi.fn(), testImageHosting: vi.fn(),
  setImageHostingDefault: vi.fn(), deleteImageHosting: vi.fn(),
}))
enableAutoUnmount(afterEach)
beforeEach(() => {
  setActivePinia(createPinia())
  const app = useAppStore()
  app.darkMode = false
  app.applyTheme()
  vi.clearAllMocks()
  vi.mocked(fetchUserAgents).mockResolvedValue({ content: '<!-- 交流偏好 -->', revision: 'a'.repeat(64) })
  vi.mocked(fetchForbiddenCategories).mockResolvedValue({ platform: 'yandex', filename: '', imported_at: '', categories: [] })
  const watermark = { text: '', image_data_url: '', opacity: 25, size_percent: 30, density: 5, color: '#737373' }
  vi.mocked(fetchSystemSettings).mockResolvedValue({ orders_auto_sync_interval_hours: 5, watermark, wechat_forward_types: [] })
  vi.mocked(saveSystemSettings).mockResolvedValue({ orders_auto_sync_interval_hours: 12, watermark, wechat_forward_types: [] })
  vi.mocked(fetchYandexWarehouses).mockResolvedValue({ ok: true, account: { business_id: '', campaign_id: '', placement_type: '', stock_update_mode: '' }, revision: 0, bindings: [], binding_warnings: [], modes: [], warehouses: [], routes: [], carriers: [], checked_at: '', error: '' })
  vi.mocked(fetchManagedSettings).mockResolvedValue({
    ok: true, revision: 'rev-1', alibaba_api_application: 'alibaba',
    exchange_rates: { exchange_rate_api_url: 'https://rates.example/api', exchange_rate_timeout_seconds: '10', exchange_rate_cache_ttl_seconds: '3600' },
    alibaba: { app_key: '', redirect_uri: '', secret_configured: false },
    alibaba_crossborder: { app_key: '', redirect_uri: '', secret_configured: false },
    onebound: { api_key: '', secret_configured: false, initial_grant: 200 },
    crossborderbus_secret_configured: false,
    gemini_canvas_url: 'https://gemini.google.com/share/a3adc37e7008',
  })
  vi.mocked(fetchOneBoundQuota).mockResolvedValue({
    ok: true, configured: false, is_admin: true, active_provider: 'alibaba', authorized: false,
    buyer: null, buyers: [], key: null, entries: [],
  })
  vi.mocked(loadImageHosting).mockResolvedValue({ default_profile_id: '', profiles: [] })
})
afterEach(() => {
  const app = useAppStore()
  app.darkMode = false
  app.applyTheme()
  vi.useRealTimers()
})
const global = { stubs: { teleport: true } }
describe('系统设置入口与同步间隔', () => {
  it('用户自定义指令支持独立深链，进入后才读取', async () => {
    const route = reactive({ query: { settings_section: 'general' } })
    const wrapper = mount(SystemSettingsPanel, { global: { ...global, provide: { [routeLocationKey as symbol]: route } } })
    await flushPromises()
    expect(fetchUserAgents).not.toHaveBeenCalled()
    route.query.settings_section = 'user_agents'
    await flushPromises()
    expect(wrapper.get('#system-settings-tab-user_agents').attributes('aria-selected')).toBe('true')
    expect((wrapper.get('textarea#user-agents-content').element as HTMLTextAreaElement).value).toContain('交流偏好')
    expect(fetchUserAgents).toHaveBeenCalledTimes(1)
  })
  it('禁售类目支持深链，进入后才读取名单', async () => {
    const route = reactive({ query: { settings_section: 'general' } })
    const wrapper = mount(SystemSettingsPanel, { global: { ...global, provide: { [routeLocationKey as symbol]: route } } })
    await flushPromises()
    expect(fetchForbiddenCategories).not.toHaveBeenCalled()
    route.query.settings_section = 'forbidden_categories'
    await flushPromises()
    expect(wrapper.get('#system-settings-tab-forbidden_categories').attributes('aria-selected')).toBe('true')
    expect(wrapper.get('#system-settings-panel-forbidden_categories').text()).toContain('Yandex 禁售类目')
    expect(fetchForbiddenCategories).toHaveBeenCalledTimes(1)
  })
  it('Yandex 发布仓库有独立深链入口，进入后才读取关联配置', async () => {
    const route = reactive({ query: { settings_section: 'general' } })
    const wrapper = mount(SystemSettingsPanel, { global: { ...global, provide: { [routeLocationKey as symbol]: route } } })
    await flushPromises()
    expect(fetchYandexWarehouses).not.toHaveBeenCalled()
    route.query.settings_section = 'yandex_warehouses'
    await flushPromises()
    expect(wrapper.get('#system-settings-tab-yandex_warehouses').attributes('aria-selected')).toBe('true')
    expect(wrapper.get('#system-settings-panel-yandex_warehouses').text()).toContain('设置仓库运输模式')
    expect(fetchYandexWarehouses).toHaveBeenCalledWith(false)
  })
  it.each([false, true])('侧栏收起=%s 时底部入口仍可导航', async collapsed => {
    const wrapper = mount(AppSidebar, {
      props: { items: [], activeKey: 'orders', collapsed },
      global: { stubs: { RouterLink: true } },
    })
    expect(wrapper.classes('workspace-sidebar--collapsed')).toBe(collapsed)
    const toggle = wrapper.get(`[aria-label="${collapsed ? '展开导航' : '收起导航'}"]`)
    expect(toggle.attributes('aria-expanded')).toBe(String(!collapsed))
    await toggle.trigger('click')
    expect(wrapper.emitted('toggleCollapse')).toHaveLength(1)
    expect(fetchSystemSettings).not.toHaveBeenCalled()
    await wrapper.get('[aria-label="系统设置"]').trigger('click'); await flushPromises()
    expect(wrapper.emitted('navigate')).toEqual([['systemSettings']])
    expect(wrapper.find('dialog').exists()).toBe(false)
    expect(wrapper.find('select').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('浅色模式')
    expect(wrapper.text()).not.toContain('深色模式')
    await wrapper.setProps({ activeKey: 'systemSettings' })
    expect(wrapper.get('[aria-label="系统设置"]').attributes('aria-current')).toBe('page')
  })
  it('颜色立即生效并保存，系统配置读取失败也可调整', async () => {
    vi.mocked(fetchSystemSettings).mockRejectedValue(new Error('系统设置读取失败'))
    const persist = vi.spyOn(window.localStorage, 'setItem')
    const wrapper = mount(SystemSettingsPanel, { global }); await flushPromises()
    await wrapper.get('#settings-color-mode').setValue('dark')
    expect(document.documentElement.classList.contains('dark')).toBe(true)
    expect(persist).toHaveBeenCalledWith('theme', 'dark')
    await wrapper.get('#settings-color-mode').setValue('light')
    expect(document.documentElement.classList.contains('dark')).toBe(false)
    expect(persist).toHaveBeenCalledWith('theme', 'light')
    expect(wrapper.find('#settings-ui-language').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('界面语言')
    expect(saveSystemSettings).not.toHaveBeenCalled()
    wrapper.unmount()
    const reopened = mount(SystemSettingsPanel, { global }); await flushPromises()
    expect(reopened.get('#settings-color-mode').element).toHaveProperty('value', 'light')
    persist.mockRestore()
  })
  it('默认展示通用设置，不挂载已退役的中断与恢复及同步策略页签', async () => {
    const wrapper = mount(SystemSettingsPanel, { global }); await flushPromises()
    expect(wrapper.text()).not.toContain('物流设置')
    expect(wrapper.text()).not.toContain('中断与恢复')
    expect(wrapper.text()).not.toContain('同步与重试策略')
    expect(wrapper.find('#system-settings-tab-interruptions').exists()).toBe(false)
    expect(wrapper.find('#system-settings-tab-sync_policies').exists()).toBe(false)
    expect(wrapper.get('#system-settings-tab-general').attributes('aria-selected')).toBe('true')
  })
  it('关于支持深链打开，读取设置失败也能查看公司与协议', async () => {
    vi.mocked(fetchSystemSettings).mockRejectedValue(new Error('后端离线'))
    const route = reactive({ query: { settings_section: 'about' } })
    const wrapper = mount(SystemSettingsPanel, { global: { ...global, provide: { [routeLocationKey as symbol]: route } } })
    await flushPromises()
    expect(wrapper.get('#system-settings-tab-about').attributes('aria-selected')).toBe('true')
    expect(wrapper.get('#system-settings-panel-about').text()).toContain('成都环岛公鹿科技有限公司')
    await wrapper.get('[data-testid="view-agreement"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('dialog').text()).toContain('软件许可与服务协议')
    await wrapper.get('dialog').trigger('cancel')
    await wrapper.get('[data-testid="view-privacy"]').trigger('click')
    expect(wrapper.get('dialog').text()).toContain('隐私政策')
    route.query.settings_section = 'general'
    await flushPromises()
    expect(wrapper.find('dialog').exists()).toBe(false)
    expect(wrapper.get('#system-settings-tab-general').attributes('aria-selected')).toBe('true')
  })
  it('保存有效间隔，低于 5 小时不可提交，失败保留输入', async () => {
    const wrapper = mount(SystemSettingsPanel, { global }); await flushPromises()
    await wrapper.get('input').setValue(4)
    await wrapper.get('form').trigger('submit'); expect(saveSystemSettings).not.toHaveBeenCalled()
    await wrapper.get('input').setValue(12)
    await wrapper.get('form').trigger('submit'); await flushPromises()
    expect(saveSystemSettings).toHaveBeenCalledWith({ orders_auto_sync_interval_hours: 12 })
    expect(wrapper.text()).toContain('设置已保存')
    vi.mocked(saveSystemSettings).mockRejectedValueOnce(new Error('保存失败'))
    await wrapper.get('input').setValue(24); await wrapper.get('form').trigger('submit'); await flushPromises()
    expect(wrapper.text()).toContain('保存失败')
    expect(wrapper.get('input').element).toHaveProperty('value', '24')
  })
  it('管理员共享设置包含图片托管，仅管理员可见并按需加载', async () => {
    const account = useAccountStore()
    account.user = { id: 'u1', email: 'user@example.com', display_name: '普通用户', role: 'user', status: 'enabled', created_at: 0, updated_at: 0 }
    const userRoute = reactive({ query: { settings_section: 'managed' } })
    const userWrapper = mount(SystemSettingsPanel, { global: { ...global, provide: { [routeLocationKey as symbol]: userRoute } } })
    await flushPromises()
    expect(userWrapper.find('#system-settings-tab-managed').exists()).toBe(false)
    expect(userWrapper.get('#system-settings-tab-general').attributes('aria-selected')).toBe('true')
    expect(loadImageHosting).not.toHaveBeenCalled()
    userWrapper.unmount()

    account.user = { id: 'a1', email: 'admin@example.com', display_name: '管理员', role: 'admin', status: 'enabled', created_at: 0, updated_at: 0 }
    const adminRoute = reactive({ query: { settings_section: 'general' } })
    const adminWrapper = mount(SystemSettingsPanel, { global: { ...global, provide: { [routeLocationKey as symbol]: adminRoute } } })
    await flushPromises()
    expect(adminWrapper.find('#system-settings-tab-managed').exists()).toBe(true)
    expect(loadImageHosting).not.toHaveBeenCalled()
    adminRoute.query.settings_section = 'managed'
    await flushPromises()
    expect(adminWrapper.get('#system-settings-tab-managed').attributes('aria-selected')).toBe('true')
    expect(adminWrapper.get('#system-settings-panel-managed').text()).toContain('管理员共享设置')
    expect(adminWrapper.get('#system-settings-panel-managed').text()).toContain('图片托管（管理员共享）')
    expect(loadImageHosting).toHaveBeenCalledTimes(1)
  })
})
