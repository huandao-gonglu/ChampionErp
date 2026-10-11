import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { reactive } from 'vue'
import { routeLocationKey, type RouteLocationNormalizedLoaded } from 'vue-router'
import CrossborderBusSettingsPanel from '../CrossborderBusSettingsPanel.vue'
import { busCommand, fetchBusSettings } from '@/api/fulfillment'
import type { BusSettings } from '@/types/fulfillment'
import { fetchLogisticsSettings } from '@/api/logistics'

vi.mock('@/api/fulfillment', () => ({ busCommand: vi.fn(), fetchBusSettings: vi.fn() }))
vi.mock('@/api/logistics', async importOriginal => ({ ...await importOriginal<typeof import('@/api/logistics')>(), fetchLogisticsSettings: vi.fn() }))
enableAutoUnmount(afterEach)
const settings = (): BusSettings => ({ authorized: true, secret_configured: true, user_name: 'user', expires_at: 0, catalog: { checked_at: 1791273661, sections: [{ section_id: 143, section_name: 'Yandex', storehouse_list: [{ id: 54, name: '122义乌优易仓', code: '122' }] }] }, rules: [], delivery_choices: [] })
beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(fetchBusSettings).mockResolvedValue(settings())
  vi.mocked(busCommand).mockResolvedValue(settings())
  vi.mocked(fetchLogisticsSettings).mockResolvedValue({ ok: true, authorized: true, warehouses: [{ id: 54, name: '122义乌优易仓', code: '122', sections: [] }], addresses: [], carriers: [] })
})
describe('跨境巴士物流设置', () => {
  it('默认履约只展示物流设置，无平台配送来源也能管理收货地址', async () => {
    const wrapper = mount(CrossborderBusSettingsPanel)
    await flushPromises()
    expect(wrapper.text()).toContain('已读取 1 个合作渠道、1 个合作仓库')
    expect(fetchLogisticsSettings).not.toHaveBeenCalled()
    await wrapper.findAll('button').find(button => button.text() === '默认履约')!.trigger('click')
    await flushPromises()
    expect(wrapper.get('[aria-label="物流设置"]').text()).toContain('新增收货地址')
    expect(fetchLogisticsSettings).toHaveBeenCalledWith(true)
    const warehouses = wrapper.get('[aria-label="合作仓库"]')
    expect(warehouses.text()).toContain('122义乌优易仓')
    expect(wrapper.text()).not.toContain('尚无可用配送来源')
    expect(wrapper.text()).not.toContain('默认履约方案')
    expect(wrapper.text()).not.toContain('新增方案')
    expect(wrapper.find('[aria-label="已合作仓库"]').exists()).toBe(false)
    expect(wrapper.find('table').exists()).toBe(false)
    await wrapper.findAll('button').find(button => button.text() === '同步合作仓库')!.trigger('click')
    await flushPromises()
    expect(fetchLogisticsSettings).toHaveBeenCalledTimes(2)
    expect(busCommand).not.toHaveBeenCalled()
  })
  it('接口成功但合作列表为空时明确提示合作关系，失败时显示错误', async () => {
    vi.mocked(fetchLogisticsSettings).mockResolvedValue({ ok: true, authorized: true, warehouses: [], addresses: [], carriers: [] })
    const wrapper = mount(CrossborderBusSettingsPanel)
    await flushPromises()
    await wrapper.findAll('button').find(button => button.text() === '默认履约')!.trigger('click')
    await flushPromises()
    expect(wrapper.get('[aria-label="物流设置"]').text()).toContain('当前账号暂无已合作仓库')
    vi.mocked(fetchLogisticsSettings).mockRejectedValue(new Error('合作仓库读取失败'))
    await wrapper.findAll('button').find(button => button.text() === '同步合作仓库')!.trigger('click')
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('合作仓库读取失败')
  })
  it('深链直接打开默认履约，路由变化时切换对应页签', async () => {
    const route = reactive({ query: { auth_section: 'crossborderbus', bus_section: 'rules' } })
    const wrapper = mount(CrossborderBusSettingsPanel, { global: { provide: { [routeLocationKey as symbol]: route as unknown as RouteLocationNormalizedLoaded } } })
    await flushPromises()
    expect(wrapper.get('[aria-label="物流设置"]').text()).toContain('122义乌优易仓')
    expect(wrapper.text()).not.toContain('默认履约方案')
    route.query.bus_section = 'account'
    await flushPromises()
    expect(wrapper.find('[aria-label="物流设置"]').exists()).toBe(false)
    expect(wrapper.get('form').text()).toContain('跨境巴士账号授权')
    route.query.bus_section = 'rules'
    await flushPromises()
    expect(wrapper.find('[aria-label="物流设置"]').exists()).toBe(true)
  })
})
