import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import SyncPolicySettingsPanel from '../SyncPolicySettingsPanel.vue'
import { fetchSyncPolicies, saveSystemSettings } from '@/api/systemSettings'
import type { BackendSyncPolicyCatalog } from '@/types/workflow.generated'

vi.mock('@/api/systemSettings', () => ({ fetchSyncPolicies: vi.fn(), saveSystemSettings: vi.fn() }))
const catalog: BackendSyncPolicyCatalog = { items: [
  { key: 'confirmation_default', title: '发布结果确认', description: '平台受理后的只读确认', effect: '新计划生效', editable: true,
    values: { interval: 60, max_checks: 10, lifetime: 900 }, fields: [
      { key: 'interval', label: '查询间隔', default: 60, minimum: 30, maximum: 86400, step: 1, unit: '秒' },
      { key: 'max_checks', label: '最多查询轮数', default: 10, minimum: 1, maximum: 1000, step: 1, unit: '轮' },
      { key: 'lifetime', label: '确认期限', default: 900, minimum: 60, maximum: 604800, step: 1, unit: '秒' },
    ] },
  { key: 'newapi_account', title: '账号待同步重试', description: '管理员维护', effect: '下一轮生效', editable: false,
    values: { interval: 30 }, fields: [{ key: 'interval', label: '同步间隔', default: 30, minimum: 5, maximum: 3600, step: 1, unit: '秒' }] },
] }
beforeEach(() => { vi.resetAllMocks(); vi.mocked(fetchSyncPolicies).mockResolvedValue(structuredClone(catalog)) })

describe('同步与重试策略', () => {
  it('按完整目录展示字段，显式保存且不提交只读管理员策略', async () => {
    const wrapper = mount(SyncPolicySettingsPanel)
    await flushPromises()
    expect(wrapper.findAll('input')).toHaveLength(4)
    expect(wrapper.text()).toContain('确认期限')
    expect(wrapper.get('#sync-confirmation_default-interval').attributes()).toMatchObject({ min: '30', max: '86400' })
    expect(wrapper.findAll('fieldset')[1]!.attributes('disabled')).toBeDefined()
    await wrapper.get('#sync-confirmation_default-max_checks').setValue(12)
    expect(saveSystemSettings).not.toHaveBeenCalled()
    await wrapper.get('form').trigger('submit'); await flushPromises()
    expect(saveSystemSettings).toHaveBeenCalledWith({ sync_policies: { confirmation_default: { interval: 60, max_checks: 12, lifetime: 900 } } })
    expect(wrapper.text()).toContain('同步策略已保存')
    wrapper.unmount()
  })
  it('保存失败保留编辑值和错误提示', async () => {
    vi.mocked(saveSystemSettings).mockRejectedValue(new Error('确认期限过短'))
    const wrapper = mount(SyncPolicySettingsPanel); await flushPromises()
    await wrapper.get('#sync-confirmation_default-lifetime').setValue(60)
    await wrapper.get('form').trigger('submit'); await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('确认期限过短')
    expect((wrapper.get('#sync-confirmation_default-lifetime').element as HTMLInputElement).value).toBe('60')
    wrapper.unmount()
  })
  it('读取失败支持重新读取，不发送保存请求', async () => {
    vi.mocked(fetchSyncPolicies).mockRejectedValueOnce(new Error('读取失败'))
    const wrapper = mount(SyncPolicySettingsPanel); await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('读取失败')
    await wrapper.get('[role="alert"] button').trigger('click'); await flushPromises()
    expect(wrapper.findAll('input')).toHaveLength(4)
    expect(saveSystemSettings).not.toHaveBeenCalled()
    wrapper.unmount()
  })
})
