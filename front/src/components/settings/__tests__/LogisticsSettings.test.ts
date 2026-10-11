import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import LogisticsSettingsPanel from '../LogisticsSettingsPanel.vue'
import LogisticsAddressEditor from '../LogisticsAddressEditor.vue'
import { emptyLogisticsAddress, fetchLogisticsSettings, saveLogisticsAddress, parseLogisticsAddress } from '@/api/logistics'
import type { LogisticsAddress, LogisticsSettings } from '@/api/logistics'
vi.mock('@/api/logistics', async importOriginal => ({ ...await importOriginal<typeof import('@/api/logistics')>(), fetchLogisticsSettings: vi.fn(), saveLogisticsAddress: vi.fn(), parseLogisticsAddress: vi.fn() }))
enableAutoUnmount(afterEach)
const first: LogisticsAddress = { ...emptyLogisticsAddress(10), id: 'addr-1', revision: 3, warehouse_name: '义乌合作仓', name: '义乌地址', updated_at: '', migrated: false,
  address: { ...emptyLogisticsAddress(10).address, fullName: '收件人', mobile: '13800000000', provinceText: '浙江', cityText: '金华', areaText: '义乌', address: '路 1 号' },
  rules: [{ platform: 'yandex', carrier: 'CEL' }] }
const settings: LogisticsSettings = { ok: true, authorized: true, warehouses: [{ id: 10, name: '义乌合作仓', code: '1', sections: [{ id: 1, name: 'Yandex', platform: 'yandex' }] }, { id: 20, name: '另一合作仓', code: '2', sections: [{ id: 1, name: 'Yandex', platform: 'yandex' }] }],
  addresses: [first, { ...first, id: 'addr-2', name: '深圳地址', enabled: false }], carriers: [{ value: 'CEL', label: 'CEL', platform: 'yandex' }, { value: 'UNI', label: 'UNI', platform: 'yandex' }] }
beforeEach(() => { vi.resetAllMocks(); vi.mocked(fetchLogisticsSettings).mockResolvedValue(structuredClone(settings)); vi.mocked(saveLogisticsAddress).mockResolvedValue(structuredClone(settings)) })
it('同一合作仓展示多个地址；编辑保存包含所属仓和版本，不携带只读字段', async () => {
  const wrapper = mount(LogisticsSettingsPanel); await flushPromises()
  expect(wrapper.text()).toContain('义乌地址'); expect(wrapper.text()).toContain('深圳地址 · 已停用')
  await wrapper.findAll('button').find(b => b.text() === '编辑地址')!.trigger('click')
  await wrapper.get('[aria-label="地址名称"]').setValue('新名称')
  await wrapper.get('form').trigger('submit'); await flushPromises()
  expect(saveLogisticsAddress).toHaveBeenCalledWith(expect.objectContaining({ id: 'addr-1', revision: 3, warehouse_id: 10, name: '新名称' }))
  expect(vi.mocked(saveLogisticsAddress).mock.calls[0]![0]).not.toHaveProperty('warehouse_name')
  await wrapper.get('[aria-label="合作仓库"]').setValue('20')
  expect(wrapper.text()).toContain('此合作仓尚未配置收货地址')
})
it('设置支持多个物流商，地址编辑不引入运输方式或交货节点', async () => {
  const wrapper = mount(LogisticsAddressEditor, { props: { initial: first, settings, busy: false } })
  await wrapper.get('[aria-label="UNI"]').setValue(true)
  await wrapper.get('[aria-label="详细地址"]').setValue('新地址')
  await wrapper.get('form').trigger('submit')
  expect(wrapper.emitted('save')![0]![0]).toEqual(expect.objectContaining({ rules: [{ platform: 'yandex', carrier: 'CEL' }, { platform: 'yandex', carrier: 'UNI' }] }))
  expect(wrapper.text()).not.toContain('运输服务范围')
  expect(wrapper.text()).not.toContain('交货节点')
  expect(wrapper.text()).not.toContain('Express')
  await wrapper.get('[aria-label="CEL"]').setValue(false)
  await wrapper.get('form').trigger('submit')
  expect(wrapper.emitted('save')!.at(-1)![0]).toEqual(expect.objectContaining({ rules: [{ platform: 'yandex', carrier: 'UNI' }] }))
  expect(first.address.address).toBe('路 1 号')
})
it('保存冲突保留编辑内容并显示错误，不关闭编辑器', async () => {
  vi.mocked(saveLogisticsAddress).mockRejectedValue(new Error('收货地址资料已更新，请刷新后重试'))
  const wrapper = mount(LogisticsSettingsPanel); await flushPromises()
  await wrapper.findAll('button').find(b => b.text() === '编辑地址')!.trigger('click')
  await wrapper.get('[aria-label="地址名称"]').setValue('尚未保存')
  await wrapper.get('form').trigger('submit'); await flushPromises()
  expect(wrapper.get('[role="alert"]').text()).toContain('资料已更新')
  expect((wrapper.get('[aria-label="地址名称"]').element as HTMLInputElement).value).toBe('尚未保存')
})

it('解析填入地址且可手动核对，不改变名称或物流商，不自动保存', async () => {
  const parsed = { ...first.address, fullName: '张三', mobile: '13800138000', address: '科技路1号' }
  vi.mocked(parseLogisticsAddress).mockResolvedValue({ ok: true, address: parsed, warnings: [] })
  const wrapper = mount(LogisticsAddressEditor, { props: { initial: first, settings, busy: false } })
  const parseButton = () => wrapper.findAll('button').find(b => b.text() === '解析并填入地址')!
  expect(parseButton().attributes('disabled')).toBeDefined()
  const text = '张三 13800138000 广东省深圳市南山区科技路1号'
  await wrapper.get('[aria-label="粘贴完整地址"]').setValue(text)
  await parseButton().trigger('click'); await flushPromises()
  expect(parseLogisticsAddress).toHaveBeenCalledWith(text)
  expect((wrapper.get('[aria-label="收货人"]').element as HTMLInputElement).value).toBe('张三')
  expect(wrapper.emitted('save')).toBeUndefined()
  expect(saveLogisticsAddress).not.toHaveBeenCalled()
  await wrapper.get('[aria-label="详细地址"]').setValue('科技路1号 A 栋')
  await wrapper.get('form').trigger('submit')
  expect(wrapper.emitted('save')![0]![0]).toEqual(expect.objectContaining({ name: first.name, rules: first.rules, address: { ...parsed, address: '科技路1号 A 栋' } }))
})

it('解析期间不能提交，缺失字段留空，不混入旧地址字段', async () => {
  let resolve!: (result: Awaited<ReturnType<typeof parseLogisticsAddress>>) => void
  vi.mocked(parseLogisticsAddress).mockImplementation(() => new Promise(done => { resolve = done }))
  const wrapper = mount(LogisticsAddressEditor, { props: { initial: first, settings, busy: false } })
  await wrapper.get('[aria-label="粘贴完整地址"]').setValue('多个收件人的仓库资料')
  await wrapper.findAll('button').find(b => b.text() === '解析并填入地址')!.trigger('click')
  await wrapper.get('form').trigger('submit')
  expect(wrapper.emitted('save')).toBeUndefined()
  expect(wrapper.get('fieldset').attributes('disabled')).toBeDefined()
  resolve({ ok: true, address: { ...emptyLogisticsAddress(10).address, address: '路 2 号' }, warnings: ['未识别：收货人、电话，请手动补全'] })
  await flushPromises()
  expect(wrapper.text()).toContain('未识别：收货人、电话')
  expect((wrapper.get('[aria-label="收货人"]').element as HTMLInputElement).value).toBe('')
  expect((wrapper.get('[aria-label="手机号"]').element as HTMLInputElement).value).toBe('')
})

it('解析失败保留原地址和粘贴文本，可继续手动编辑', async () => {
  vi.mocked(parseLogisticsAddress).mockRejectedValue(new Error('地址解析失败'))
  const wrapper = mount(LogisticsAddressEditor, { props: { initial: first, settings, busy: false } })
  await wrapper.get('[aria-label="粘贴完整地址"]').setValue('待解析文本')
  await wrapper.findAll('button').find(b => b.text() === '解析并填入地址')!.trigger('click'); await flushPromises()
  expect(wrapper.get('[role="alert"]').text()).toBe('地址解析失败')
  expect((wrapper.get('[aria-label="粘贴完整地址"]').element as HTMLTextAreaElement).value).toBe('待解析文本')
  expect((wrapper.get('[aria-label="详细地址"]').element as HTMLInputElement).value).toBe(first.address.address)
  expect(wrapper.get('fieldset').attributes('disabled')).toBeUndefined()
})
