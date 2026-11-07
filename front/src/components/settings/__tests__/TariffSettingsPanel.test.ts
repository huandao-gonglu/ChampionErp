import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { useAccountStore } from '@/stores/account'
import TariffSettingsPanel from '../TariffSettingsPanel.vue'
import WorkspaceDialog from '@/components/shared/WorkspaceDialog.vue'
import { fetchTariffSettings, fetchTariffRules, importTariffFile, readTariffFile, upgradeTariff, type TariffSettings } from '@/api/managedSettings'

vi.mock('@/api/managedSettings', () => ({ fetchTariffSettings: vi.fn(), fetchTariffRules: vi.fn(), importTariffFile: vi.fn(), readTariffFile: vi.fn(), upgradeTariff: vi.fn() }))
enableAutoUnmount(afterEach)
const shared = { version: 'yandex-new', effective_date: '', rules_count: 2, error: '' }
const custom: TariffSettings = { platform: 'yandex', source: 'personal', version: 'yandex-custom', effective_date: '', rules_count: 1, error: '', shared, update_available: true }
const followed: TariffSettings = { ...custom, ...shared, source: 'shared', update_available: false }
const open = () => mount(TariffSettingsPanel, { global: { stubs: { teleport: true } } })
const click = async (wrapper: ReturnType<typeof open>, text: string) => {
  await wrapper.findAll('button').find(button => button.text() === text)!.trigger('click')
  await flushPromises()
}
beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  vi.mocked(fetchTariffSettings).mockResolvedValue([structuredClone(custom)])
  vi.mocked(fetchTariffRules).mockResolvedValue({ ok: true, platform: 'yandex', source: 'personal', version: custom.version,
    filename: '个人表.xlsx', sheet: '', imported_at: '', effective_date: '', rules: [], warnings: [] })
  vi.mocked(upgradeTariff).mockResolvedValue([followed])
  vi.mocked(readTariffFile).mockResolvedValue('encoded-file')
  vi.mocked(importTariffFile).mockResolvedValue({ items: [custom], diff: { after_version: 'new', added: [], removed: [], changed: [], policy_change: null, status: '校验通过' } })
})

describe('国际物流运费表', () => {
  it('可分别查看当前表和管理员默认表', async () => {
    const wrapper = open(); await flushPromises()
    await click(wrapper, '查看当前运费表')
    expect(fetchTariffRules).toHaveBeenLastCalledWith('yandex', 'effective')
    await click(wrapper, '关闭')
    await click(wrapper, '查看管理员默认表')
    expect(fetchTariffRules).toHaveBeenLastCalledWith('yandex', 'shared')
  })
  it('普通账号可导入个人表，覆盖管理员更新前需要本人确认', async () => {
    const wrapper = open(); await flushPromises()
    expect(wrapper.text()).toContain('管理员已发布更新')
    expect(wrapper.text()).not.toContain('发布默认运费表')
    await click(wrapper, '升级并覆盖个人表')
    expect(upgradeTariff).not.toHaveBeenCalled()
    await click(wrapper, '继续使用个人表')
    expect(upgradeTariff).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('yandex-custom')
    await click(wrapper, '升级并覆盖个人表')
    await click(wrapper, '确认覆盖并跟随')
    expect(upgradeTariff).toHaveBeenCalledWith(custom)
    expect(wrapper.text()).toContain('管理员默认 · 自动更新')
  })

  it('管理员发布也先校验预览，确认时使用同一文件及预期版本', async () => {
    useAccountStore().user = { id: 'admin', email: 'admin@example.com', display_name: '管理员', role: 'admin', status: 'enabled', created_at: 0, updated_at: 0 }
    const wrapper = open(); await flushPromises()
    await click(wrapper, '发布默认运费表')
    const input = wrapper.get('input[type="file"]')
    Object.defineProperty(input.element, 'files', { value: [new File(['file'], '费率.xlsx')] })
    await input.trigger('change')
    await click(wrapper, '校验并预览')
    expect(importTariffFile).toHaveBeenCalledTimes(1)
    expect(importTariffFile).toHaveBeenLastCalledWith(expect.objectContaining({ scope: 'shared', preview: true, expected_version: shared.version, content_base64: 'encoded-file' }))
    await click(wrapper, '确认发布')
    expect(importTariffFile).toHaveBeenLastCalledWith(expect.objectContaining({ scope: 'shared', preview: false, expected_version: shared.version, content_base64: 'encoded-file' }))
  })

  it('导入弹窗中从内容拖出不关闭，正常遮罩点击关闭', async () => {
    const wrapper = open(); await flushPromises()
    await click(wrapper, '导入个人运费表')
    const dialog = wrapper.get('dialog')
    const pointer = { isPrimary: true, button: 0, pointerId: 1 }
    await wrapper.get('input[type="file"]').trigger('pointerdown', pointer)
    await dialog.trigger('pointerup', pointer)
    expect((dialog.element as HTMLDialogElement).open).toBe(true)
    await dialog.trigger('pointerdown', pointer)
    await dialog.trigger('pointerup', pointer)
    await flushPromises()
    expect(wrapper.findAllComponents(WorkspaceDialog)[0].emitted('close')).toHaveLength(1)
    expect(wrapper.findAllComponents(WorkspaceDialog)[0].props('open')).toBe(false)
  })
})
