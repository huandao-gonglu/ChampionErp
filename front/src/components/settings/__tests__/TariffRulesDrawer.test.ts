import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import TariffRulesDrawer from '../TariffRulesDrawer.vue'
import { fetchTariffRules, type TariffRules } from '@/api/managedSettings'

vi.mock('@/api/managedSettings', () => ({ fetchTariffRules: vi.fn() }))
enableAutoUnmount(afterEach)
const rule: TariffRules['rules'][number] = {
  id: 'route-1', name: 'CEL Economy Extra Small', carrier: 'CEL', service: 'Economy', transport: '陆运', group: 'Extra Small',
  currency: 'RUB', fixed: '78', per_g: '0.287', step_g: '1', min_g: '', max_g: '500',
  price_min: '', price_max: '1500', price_currency: 'RUB', max_edge_cm: '60', max_sum_cm: '90',
  volume_divisor_g: '', battery: true, liquid: null, source_cell: '表1!A2:B2',
}
const saved: TariffRules = { ok: true, platform: 'yandex', source: 'shared', version: 'yandex-version',
  filename: '运费表.xlsx', sheet: '表1', imported_at: '2026-11-07T00:00:00Z', effective_date: '',
  rules: Array.from({ length: 21 }, (_, index) => ({ ...rule, id: String(index), name: index === 20 ? 'GUOO Economy Extra Small' : rule.name, carrier: index === 20 ? 'GUOO' : 'CEL' })), calculation_notes: [], warnings: ['规则来源说明'] }
const open = () => mount(TariffRulesDrawer, { props: { platform: 'yandex', scope: 'effective' }, global: { stubs: { teleport: true } } })
beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(fetchTariffRules).mockResolvedValue(structuredClone(saved))
})

it('查看已导入规则，展示真实单位、适用限制并支持搜索和分页', async () => {
  const wrapper = open(); await flushPromises()
  expect(wrapper.text()).toContain('运费表.xlsx')
  expect(wrapper.text()).toContain('每克 0.287 RUB')
  expect(wrapper.text()).toContain('≤ 500 g')
  expect(wrapper.text()).toContain('≤ 1500 RUB')
  expect(wrapper.text()).toContain('液体 · 未明确准入')
  expect(wrapper.findAll('tbody tr')).toHaveLength(20)
  await wrapper.findAll('button').find(button => button.text() === '下一页')!.trigger('click')
  expect(wrapper.findAll('tbody tr')).toHaveLength(1)
  expect(wrapper.get('tbody').text()).toContain('GUOO')
  await wrapper.get('input[type="search"]').setValue('CEL')
  expect(wrapper.findAll('tbody tr')).toHaveLength(20)
  expect(wrapper.text()).toContain('1 / 1')
  expect(wrapper.text()).toContain('规则来源说明')
})

it('内容按下后拖到遮罩松开继续查看，正常遮罩点击可关闭', async () => {
  const wrapper = open(); await flushPromises()
  const pointer = { isPrimary: true, button: 0, pointerId: 1 }
  await wrapper.get('input').trigger('pointerdown', pointer)
  await wrapper.get('dialog').trigger('pointerup', pointer)
  expect(wrapper.emitted('close')).toBeUndefined()
  await wrapper.get('dialog').trigger('pointerdown', pointer)
  await wrapper.get('dialog').trigger('pointerup', pointer)
  expect(wrapper.emitted('close')).toHaveLength(1)
})

it('OGL 按 CNY 展示货值及费用，并显示体积重计算方法', async () => {
  vi.mocked(fetchTariffRules).mockResolvedValue({ ...saved, platform: 'ozon', rules: [{ ...rule,
    name: 'OGL Big', carrier: 'OGL', service: 'OGL', group: 'Big', transport: '',
    currency: 'CNY', fixed: '34.82', per_g: '0.0164', min_g: '2001', max_g: '35000',
    price_min: '135.01', price_max: '635', price_currency: 'CNY', volume_divisor_g: '12', liquid: true,
  }] })
  const wrapper = mount(TariffRulesDrawer, { props: { platform: 'ozon', scope: 'shared' }, global: { stubs: { teleport: true } } })
  await flushPromises()
  expect(wrapper.text()).toContain('每克 0.0164 CNY')
  expect(wrapper.text()).toContain('135.01–635 CNY')
  expect(wrapper.text()).toContain('体积重为 cm³ ÷ 12（g）')
  expect(fetchTariffRules).toHaveBeenCalledWith('ozon', 'shared')
})

it('读取失败可重新读取', async () => {
  vi.mocked(fetchTariffRules).mockRejectedValueOnce(new Error('读取失败'))
  const wrapper = open(); await flushPromises()
  expect(wrapper.get('[role="alert"]').text()).toBe('读取失败')
  await wrapper.findAll('button').find(button => button.text() === '重新读取')!.trigger('click')
  await flushPromises()
  expect(wrapper.text()).toContain('运费表.xlsx')
})
