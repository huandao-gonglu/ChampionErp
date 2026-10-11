import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { reactive } from 'vue'
import { routeLocationKey } from 'vue-router'
import { i18n } from '@/i18n'
import { useAppStore } from '@/stores/app'
import SystemSettingsPanel from '../SystemSettingsPanel.vue'
import AppSidebar from '@/components/layout/AppSidebar.vue'
import { fetchSystemSettings, saveSystemSettings } from '@/api/systemSettings'
import { fetchRequestControl } from '@/api/externalRequests'
vi.mock('@/api/systemSettings', () => ({ fetchSystemSettings: vi.fn(), saveSystemSettings: vi.fn() }))
vi.mock('@/api/externalRequests', () => ({ fetchRequestControl: vi.fn(), recoverRequestBlock: vi.fn(), clearRequestHistory: vi.fn() }))
enableAutoUnmount(afterEach)
beforeEach(() => {
  setActivePinia(createPinia())
  const app = useAppStore()
  app.darkMode = false
  app.applyTheme()
  app.setUiLocale('zh')
  vi.clearAllMocks()
  vi.mocked(fetchSystemSettings).mockResolvedValue({ orders_auto_sync_interval_hours: 5 })
  vi.mocked(saveSystemSettings).mockResolvedValue({ orders_auto_sync_interval_hours: 12 })
  vi.mocked(fetchRequestControl).mockResolvedValue({ ok: true, server_time: 1800000000, total: 0, cleanup_before: 1800000000, clearable_count: 0, blocks: [], history: [] })
})
afterEach(() => {
  const app = useAppStore()
  app.darkMode = false
  app.applyTheme()
  app.setUiLocale('zh')
  vi.useRealTimers()
})
const global = { plugins: [i18n], stubs: { teleport: true } }
describe('系统设置入口与同步间隔', () => {
  it.each([false, true])('侧栏收起=%s 时底部入口仍可导航', async collapsed => {
    const wrapper = mount(AppSidebar, { props: { items: [], activeKey: 'orders', steps: [], progress: 0, collapsed }, global })
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
  it('颜色和语言立即生效并保存，系统配置读取失败也可调整', async () => {
    vi.mocked(fetchSystemSettings).mockRejectedValue(new Error('系统设置读取失败'))
    const persist = vi.spyOn(window.localStorage, 'setItem')
    const wrapper = mount(SystemSettingsPanel, { global }); await flushPromises()
    await wrapper.get('#settings-color-mode').setValue('dark')
    expect(document.documentElement.classList.contains('dark')).toBe(true)
    expect(persist).toHaveBeenCalledWith('theme', 'dark')
    await wrapper.get('#settings-color-mode').setValue('light')
    expect(document.documentElement.classList.contains('dark')).toBe(false)
    expect(persist).toHaveBeenCalledWith('theme', 'light')
    await wrapper.get('#settings-ui-language').setValue('en')
    expect(useAppStore().uiLocale).toBe('en')
    expect(i18n.global.locale.value).toBe('en')
    expect(document.documentElement.lang).toBe('en')
    expect(wrapper.text()).toContain('Appearance and language')
    expect(persist).toHaveBeenCalledWith('uiLocale', 'en')
    expect(saveSystemSettings).not.toHaveBeenCalled()
    wrapper.unmount()
    const reopened = mount(SystemSettingsPanel, { global }); await flushPromises()
    expect(reopened.get('#settings-color-mode').element).toHaveProperty('value', 'light')
    expect(reopened.get('#settings-ui-language').element).toHaveProperty('value', 'en')
    await reopened.get('#settings-ui-language').setValue('zh')
    expect(document.documentElement.lang).toBe('zh-CN')
    expect(persist).toHaveBeenCalledWith('uiLocale', 'zh')
    persist.mockRestore()
  })
  it('默认展示通用设置，切换中断页后返回保留输入并停止中断轮询', async () => {
    vi.useFakeTimers()
    const wrapper = mount(SystemSettingsPanel, { global }); await flushPromises()
    expect(wrapper.text()).not.toContain('物流设置')
    expect(wrapper.get('#system-settings-tab-general').attributes('aria-selected')).toBe('true')
    expect(fetchRequestControl).not.toHaveBeenCalled()
    await wrapper.get('input').setValue(12)
    await wrapper.get('#system-settings-tab-interruptions').trigger('click'); await flushPromises()
    expect(wrapper.get('#system-settings-tab-interruptions').attributes('aria-selected')).toBe('true')
    expect(wrapper.find('[aria-label="平台请求中断与恢复"]').exists()).toBe(true)
    expect(wrapper.get('#system-settings-form').isVisible()).toBe(false)
    expect(fetchRequestControl).toHaveBeenCalledTimes(1)
    await wrapper.get('#system-settings-tab-general').trigger('click'); await flushPromises()
    expect(wrapper.find('[aria-label="平台请求中断与恢复"]').exists()).toBe(false)
    expect(wrapper.get('input').element).toHaveProperty('value', '12')
    await vi.advanceTimersByTimeAsync(6000)
    expect(fetchRequestControl).toHaveBeenCalledTimes(1)
  })
  it('深链打开中断与恢复并保留平台定位，路由切换可返回通用设置', async () => {
    const route = reactive({ query: { settings_section: 'interruptions', request_platform: ['ozon'] } })
    const wrapper = mount(SystemSettingsPanel, { global: { ...global, provide: { [routeLocationKey as symbol]: route } } })
    await flushPromises()
    expect(wrapper.get('#system-settings-tab-interruptions').attributes('aria-selected')).toBe('true')
    expect(wrapper.text()).toContain('Ozon 当前没有活动阻断')
    route.query.settings_section = 'general'
    await flushPromises()
    expect(wrapper.get('#system-settings-tab-general').attributes('aria-selected')).toBe('true')
    expect(wrapper.find('[aria-label="平台请求中断与恢复"]').exists()).toBe(false)
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
})
