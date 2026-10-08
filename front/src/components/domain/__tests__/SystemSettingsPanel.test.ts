import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import SystemSettingsPanel from '../SystemSettingsPanel.vue'
import AppSidebar from '@/components/layout/AppSidebar.vue'
import { fetchSystemSettings, saveSystemSettings } from '@/api/systemSettings'
vi.mock('@/api/systemSettings', () => ({ fetchSystemSettings: vi.fn(), saveSystemSettings: vi.fn() }))
enableAutoUnmount(afterEach)
beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  vi.mocked(fetchSystemSettings).mockResolvedValue({ orders_auto_sync_interval_hours: 5 })
  vi.mocked(saveSystemSettings).mockResolvedValue({ orders_auto_sync_interval_hours: 12 })
})
const global = { stubs: { teleport: true } }
describe('系统设置入口与同步间隔', () => {
  it.each([false, true])('侧栏收起=%s 时底部入口仍可导航', async collapsed => {
    const wrapper = mount(AppSidebar, { props: { items: [], activeKey: 'orders', steps: [], progress: 0, collapsed }, global })
    expect(fetchSystemSettings).not.toHaveBeenCalled()
    await wrapper.get('[aria-label="系统设置"]').trigger('click'); await flushPromises()
    expect(wrapper.emitted('navigate')).toEqual([['systemSettings']])
    expect(wrapper.find('dialog').exists()).toBe(false)
    await wrapper.setProps({ activeKey: 'systemSettings' })
    expect(wrapper.get('[aria-label="系统设置"]').attributes('aria-current')).toBe('page')
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
