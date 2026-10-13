import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import OnlineOperationRecovery from '../OnlineOperationRecovery.vue'
import { onlineAction, type OnlineJob } from '@/api/onlineProducts'

vi.mock('@/api/onlineProducts', () => ({ onlineAction: vi.fn() }))
enableAutoUnmount(afterEach)
beforeEach(() => { vi.clearAllMocks(); vi.useFakeTimers(); vi.setSystemTime(new Date('2026-10-13T10:00:00Z')) })
afterEach(() => vi.useRealTimers())

function job(): OnlineJob {
  return { id: 'job-1', operation: 'stock', platform: 'yandex', status: 'outcome_unknown',
    created_at: '2026-10-13T07:50:00Z', updated_at: 'checked-version', target_id: 'listing-1',
    request: { changes: { quantity: 0 }, scope_id: 'warehouse-1' },
    result: { before: { seller_sku: 'SKU-1' }, changes: { quantity: 0 }, confirmation: { quantity: false },
      evidence: { synced_at: '2026-10-06T10:00:00Z', checked_at: '2026-10-13T10:00:00Z', observed: { quantity: 9100 } } },
  }
}
async function render(value = job()) {
  const wrapper = mount(OnlineOperationRecovery, { props: { job: value }, global: { stubs: { teleport: true } } })
  await wrapper.get('[data-testid="review-unconfirmed"]').trigger('click')
  await flushPromises()
  return wrapper
}

describe('未确认操作恢复', () => {
  it('核对当前值与目标后才可明确结束，提交绑定所见记录且不重放修改', async () => {
    const wrapper = await render()
    expect(wrapper.get('dialog').text()).toContain('9100')
    expect(wrapper.get('dialog').text()).toContain('原请求结果仍未确认')
    expect(wrapper.get('[data-testid="close-unconfirmed"]').attributes('disabled')).toBeDefined()
    expect(onlineAction).not.toHaveBeenCalled()
    vi.mocked(onlineAction).mockResolvedValue({ ...job(), status: 'closed_unconfirmed' })
    await wrapper.get('[data-testid="acknowledge-unconfirmed"]').setValue(true)
    await wrapper.get('[data-testid="close-unconfirmed"]').trigger('click')
    await flushPromises()
    expect(onlineAction).toHaveBeenCalledTimes(1)
    expect(onlineAction).toHaveBeenCalledWith('close', { job_id: 'job-1', expected_updated_at: 'checked-version', acknowledge: true })
    expect(wrapper.emitted('updated')?.[0]?.[0]).toMatchObject({ status: 'closed_unconfirmed' })
    expect(wrapper.get<HTMLDialogElement>('dialog').element.open).toBe(false)
  })

  it.each(['missing', 'expired', 'pending', 'incomplete'])('证据 %s 时不能结束，须重新查询', async problem => {
    const value = job()
    if (problem === 'missing') delete value.result.evidence
    if (problem === 'expired') value.result.evidence!.checked_at = '2026-10-13T09:54:00Z'
    if (problem === 'pending') value.result.platform_confirmation = { pending: true }
    if (problem === 'incomplete') value.result.read_errors = ['查询不完整']
    const wrapper = await render(value)
    expect(wrapper.get('[data-testid="acknowledge-unconfirmed"]').attributes('disabled')).toBeDefined()
    expect(wrapper.get('[data-testid="close-unconfirmed"]').attributes('disabled')).toBeDefined()
    expect(onlineAction).not.toHaveBeenCalled()
  })

  it('新查询失败后不能沿用上次成功的证据', async () => {
    const wrapper = await render()
    vi.mocked(onlineAction).mockRejectedValue(new Error('平台查询失败'))
    await wrapper.findAll('button').find(button => button.text() === '查询平台结果')!.trigger('click')
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toBe('平台查询失败')
    expect(wrapper.get('[data-testid="acknowledge-unconfirmed"]').attributes('disabled')).toBeDefined()
  })

  it('查询接口返回任务失败时展示具体原因，不允许沿用旧证据结束', async () => {
    const wrapper = await render()
    const failed = job()
    delete failed.result.evidence
    failed.result.error = '当前凭据失效；请按平台协议刷新或重新授权'
    vi.mocked(onlineAction).mockResolvedValue(failed)
    await wrapper.findAll('button').find(button => button.text() === '查询平台结果')!.trigger('click')
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('当前凭据失效')
    expect(wrapper.get('[data-testid="close-unconfirmed"]').attributes('disabled')).toBeDefined()
  })

  it('成功回读后不把原写入超时当作当前查询错误', async () => {
    const value = job()
    value.result.error = '原写入请求超时'
    const wrapper = await render(value)
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
    expect(wrapper.get('[data-testid="acknowledge-unconfirmed"]').attributes('disabled')).toBeUndefined()
  })

  it('查询确认已生效时无需人工结束', async () => {
    const wrapper = await render()
    vi.mocked(onlineAction).mockResolvedValue({ ...job(), status: 'confirmed' })
    await wrapper.findAll('button').find(button => button.text() === '查询平台结果')!.trigger('click')
    await flushPromises()
    expect(wrapper.get('[role="status"]').text()).toContain('已确认原修改生效')
    expect(wrapper.get('[data-testid="close-unconfirmed"]').attributes('disabled')).toBeDefined()
  })

  it('确认期间记录变化会清除勾选，不能沿用旧确认', async () => {
    const wrapper = await render()
    await wrapper.get('[data-testid="acknowledge-unconfirmed"]').setValue(true)
    await wrapper.setProps({ job: { ...job(), updated_at: 'new-version' } })
    expect(wrapper.get<HTMLInputElement>('[data-testid="acknowledge-unconfirmed"]').element.checked).toBe(false)
    expect(wrapper.get('[data-testid="close-unconfirmed"]').attributes('disabled')).toBeDefined()
  })

  it('内部按下外部松开不关闭，正常遮罩点击关闭；提交期间禁止关闭和重复提交', async () => {
    const wrapper = await render()
    const pointer = { isPrimary: true, button: 0, pointerId: 1 }
    let dialog = wrapper.get<HTMLDialogElement>('dialog')
    await wrapper.get('label').trigger('pointerdown', pointer)
    await dialog.trigger('pointerup', pointer)
    expect(dialog.element.open).toBe(true)
    await dialog.trigger('pointerdown', pointer)
    await dialog.trigger('pointerup', pointer)
    await flushPromises()
    expect(wrapper.get<HTMLDialogElement>('dialog').element.open).toBe(false)
    await wrapper.get('[data-testid="review-unconfirmed"]').trigger('click')
    await flushPromises()
    dialog = wrapper.get<HTMLDialogElement>('dialog')
    let resolve!: (value: OnlineJob) => void
    vi.mocked(onlineAction).mockImplementation(() => new Promise(done => { resolve = done }))
    await wrapper.get('[data-testid="acknowledge-unconfirmed"]').setValue(true)
    await wrapper.get('[data-testid="close-unconfirmed"]').trigger('click')
    await dialog.trigger('cancel')
    await dialog.trigger('pointerdown', pointer)
    await dialog.trigger('pointerup', pointer)
    expect(dialog.element.open).toBe(true)
    expect(wrapper.get('[data-testid="close-unconfirmed"]').attributes('disabled')).toBeDefined()
    resolve({ ...job(), status: 'closed_unconfirmed' })
    await flushPromises()
    expect(onlineAction).toHaveBeenCalledTimes(1)
  })
})
