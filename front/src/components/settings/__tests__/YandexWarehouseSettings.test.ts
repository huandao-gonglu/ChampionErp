import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import YandexWarehouseSettings from '../YandexWarehouseSettings.vue'
import { fetchYandexWarehouses, saveYandexWarehouses } from '@/api/yandexWarehouses'
import type { YandexWarehouseSettings as Settings } from '@/api/yandexWarehouses'
vi.mock('@/api/yandexWarehouses', () => ({ fetchYandexWarehouses: vi.fn(), saveYandexWarehouses: vi.fn() }))
enableAutoUnmount(afterEach)
const settings: Settings = { ok: true, account: { business_id: '222', campaign_id: '111', placement_type: 'FBS', stock_update_mode: 'business' },
  revision: 0, bindings: [], warehouses: [{ id: 31, name: 'GUOO 远端仓' }],
  routes: [{ id: 'guoo-land', name: 'GUOO 陆运', carrier: 'GUOO_GTA', transport: '陆运', service: 'Economy', group: 'Other' },
    { id: 'guoo-express', name: 'GUOO 陆空联运', carrier: 'GUOO_GTA', transport: '陆空联运', service: 'Express', group: 'Other' },
    { id: 'cel-land', name: 'CEL 陆运', carrier: 'CEL', transport: '陆运', service: 'Economy', group: 'Other' }],
  modes: [{ carrier: 'GUOO_GTA', value: 'Economy', transport: '陆运' },
    { carrier: 'GUOO_GTA', value: 'Express', transport: '陆空联运' },
    { carrier: 'GUOO_GTA', value: 'Economy Extra Small', transport: '陆运' },
    { carrier: 'CEL', value: 'Economy', transport: '陆运' }], binding_warnings: [],
  carriers: [{ value: 'GUOO_GTA', label: 'GUOO(GTA)' }, { value: 'CEL', label: 'CEL' }], checked_at: '', error: '' }
beforeEach(() => { vi.resetAllMocks(); vi.mocked(fetchYandexWarehouses).mockResolvedValue(structuredClone(settings)) })
it('仅使用远端仓库，选择物流商和运输模式，保存不带报单仓或地址', async () => {
  const wrapper = mount(YandexWarehouseSettings); await flushPromises()
  await wrapper.get('[aria-label="GUOO 远端仓 物流商"]').setValue('GUOO_GTA')
  expect(wrapper.text()).toContain('尚未选择运输模式')
  expect(wrapper.text()).not.toContain('CEL 陆运')
  await wrapper.get('[aria-label="GUOO 远端仓 运输模式"]').setValue('Economy')
  vi.mocked(saveYandexWarehouses).mockResolvedValue({ ...settings, revision: 1 })
  await wrapper.get('form').trigger('submit'); await flushPromises()
  expect(saveYandexWarehouses).toHaveBeenCalledWith({ business_id: '222', campaign_id: '111', revision: 0,
    bindings: [{ warehouse_id: 31, carrier: 'GUOO_GTA', enabled: true, shipping_mode: 'Economy' }] })
  expect(wrapper.text()).toContain('请重新核价')
})
it('远端仓库失效仍展示关联，并允许解除最后一个关联后保存', async () => {
  vi.mocked(fetchYandexWarehouses).mockResolvedValue({ ...structuredClone(settings), warehouses: [], revision: 1,
    bindings: [{ warehouse_id: 31, carrier: 'GUOO_GTA', enabled: true, shipping_mode: 'Economy' }] })
  const wrapper = mount(YandexWarehouseSettings); await flushPromises()
  expect(wrapper.text()).toContain('远端仓库未返回或已不可用')
  await wrapper.findAll('button').find(b => b.text() === '解除关联')!.trigger('click')
  vi.mocked(saveYandexWarehouses).mockResolvedValue({ ...settings, revision: 2, warehouses: [] })
  await wrapper.get('form').trigger('submit'); await flushPromises()
  expect(saveYandexWarehouses).toHaveBeenCalledWith(expect.objectContaining({ revision: 1, bindings: [] }))
})
it('失效运输模式保留显示，重新选择后才能保存', async () => {
  vi.mocked(fetchYandexWarehouses).mockResolvedValue({ ...structuredClone(settings), revision: 1,
    bindings: [{ warehouse_id: 31, carrier: 'GUOO_GTA', enabled: true, shipping_mode: 'Removed Mode' }] })
  const wrapper = mount(YandexWarehouseSettings); await flushPromises()
  expect(wrapper.text()).toContain('原运输模式已不可用')
  await wrapper.get('[aria-label="GUOO 远端仓 运输模式"]').setValue('Economy')
  vi.mocked(saveYandexWarehouses).mockResolvedValue({ ...settings, revision: 2 })
  await wrapper.get('form').trigger('submit'); await flushPromises()
  expect(saveYandexWarehouses).toHaveBeenCalledWith(expect.objectContaining({ bindings: [
    { warehouse_id: 31, carrier: 'GUOO_GTA', enabled: true, shipping_mode: 'Economy' },
  ] }))
})
it('切换物流商清除原服务；保存冲突保留修改', async () => {
  const wrapper = mount(YandexWarehouseSettings); await flushPromises()
  const carrier = wrapper.get('[aria-label="GUOO 远端仓 物流商"]')
  await carrier.setValue('GUOO_GTA')
  await wrapper.get('[aria-label="GUOO 远端仓 运输模式"]').setValue('Economy')
  await carrier.setValue('CEL')
  expect((wrapper.get('[aria-label="GUOO 远端仓 运输模式"]').element as HTMLSelectElement).value).toBe('')
  expect(wrapper.text()).not.toContain('Express')
  await wrapper.get('[aria-label="GUOO 远端仓 运输模式"]').setValue('Economy')
  vi.mocked(saveYandexWarehouses).mockRejectedValue(new Error('发布仓库物流配置已更新'))
  await wrapper.get('form').trigger('submit'); await flushPromises()
  expect(wrapper.get('[role="alert"]').text()).toContain('配置已更新')
  expect((wrapper.get('[aria-label="GUOO 远端仓 运输模式"]').element as HTMLSelectElement).value).toBe('Economy')
  expect(vi.mocked(saveYandexWarehouses).mock.calls[0][0].bindings[0].shipping_mode).toBe('Economy')
})
it('显式同步远端仓库不会调用本地报单仓接口', async () => {
  const wrapper = mount(YandexWarehouseSettings); await flushPromises()
  await wrapper.findAll('button').find(b => b.text() === '同步 Yandex 仓库')!.trigger('click'); await flushPromises()
  expect(fetchYandexWarehouses).toHaveBeenLastCalledWith(true)
  expect(wrapper.text()).toContain('GUOO 远端仓')
})
it('每个仓库只能选择一种模式，未选模式时不能保存', async () => {
  const wrapper = mount(YandexWarehouseSettings); await flushPromises()
  await wrapper.get('[aria-label="GUOO 远端仓 物流商"]').setValue('GUOO_GTA')
  expect(wrapper.get('button[type="submit"]').attributes('disabled')).toBeDefined()
  const mode = wrapper.get('[aria-label="GUOO 远端仓 运输模式"]')
  expect(mode.attributes('multiple')).toBeUndefined()
  expect(mode.text()).toContain('Economy Extra Small')
  await mode.setValue('Economy')
  await mode.setValue('Express')
  vi.mocked(saveYandexWarehouses).mockResolvedValue({ ...settings, revision: 1 })
  await wrapper.get('form').trigger('submit'); await flushPromises()
  expect(saveYandexWarehouses).toHaveBeenCalledWith(expect.objectContaining({ bindings: [
    { warehouse_id: 31, carrier: 'GUOO_GTA', enabled: true, shipping_mode: 'Express' },
  ] }))
})
it('旧配置跨模式时保留物流商并提示重新选择', async () => {
  vi.mocked(fetchYandexWarehouses).mockResolvedValue({ ...structuredClone(settings),
    bindings: [{ warehouse_id: 31, carrier: 'GUOO_GTA', enabled: true, shipping_mode: '' }],
    binding_warnings: [{ warehouse_id: 31, message: '原关联无法确定唯一运输模式，请按 Yandex 后台设置重新选择。' }] })
  const wrapper = mount(YandexWarehouseSettings); await flushPromises()
  expect((wrapper.get('[aria-label="GUOO 远端仓 物流商"]').element as HTMLSelectElement).value).toBe('GUOO_GTA')
  expect(wrapper.text()).toContain('原关联无法确定唯一运输模式')
  expect(wrapper.get('button[type="submit"]').attributes('disabled')).toBeDefined()
})
