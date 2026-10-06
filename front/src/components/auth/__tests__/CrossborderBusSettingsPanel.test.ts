import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import CrossborderBusSettingsPanel from '../CrossborderBusSettingsPanel.vue'
import { busCommand, fetchBusSettings } from '@/api/fulfillment'
import type { BusSettings } from '@/types/fulfillment'

vi.mock('@/api/fulfillment', () => ({ busCommand: vi.fn(), fetchBusSettings: vi.fn() }))
enableAutoUnmount(afterEach)
const settings = (): BusSettings => ({ authorized: true, secret_configured: true, user_name: 'user', expires_at: 0, catalog: { checked_at: 1791273661, sections: [{ section_id: 143, section_name: 'Yandex', storehouse_list: [{ id: 54, name: '122义乌优易仓', code: '122' }] }] }, rules: [], delivery_choices: [] })
beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(fetchBusSettings).mockResolvedValue(settings())
  vi.mocked(busCommand).mockResolvedValue(settings())
})
describe('跨境巴士已合作仓库', () => {
  it('无平台配送来源也能查看已拉取仓库，不将规则列表为空当成仓库为空', async () => {
    const wrapper = mount(CrossborderBusSettingsPanel)
    await flushPromises()
    expect(wrapper.text()).toContain('已读取 1 个合作渠道、1 个合作仓库')
    await wrapper.findAll('button').find(button => button.text() === '默认履约')!.trigger('click')
    const warehouses = wrapper.get('[aria-label="已合作仓库"]')
    expect(warehouses.text()).toContain('Yandex')
    expect(warehouses.text()).toContain('122义乌优易仓')
    expect(wrapper.text()).toContain('尚无可用配送来源')
    await wrapper.findAll('button').find(button => button.text() === '刷新合作仓库')!.trigger('click')
    await flushPromises()
    expect(busCommand).toHaveBeenCalledWith('catalog', {})
  })
  it('接口成功但合作列表为空时明确提示合作关系，失败时显示错误', async () => {
    vi.mocked(fetchBusSettings).mockResolvedValue({ ...settings(), catalog: { checked_at: 1791273661, sections: [] } })
    vi.mocked(busCommand).mockRejectedValue(new Error('合作仓库读取失败'))
    const wrapper = mount(CrossborderBusSettingsPanel)
    await flushPromises()
    await wrapper.findAll('button').find(button => button.text() === '默认履约')!.trigger('click')
    expect(wrapper.get('[aria-label="已合作仓库"]').text()).toContain('当前账号暂无已合作仓库')
    await wrapper.findAll('button').find(button => button.text() === '刷新合作仓库')!.trigger('click')
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('合作仓库读取失败')
  })
})
