import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import TaskQueuePanel from '../TaskQueuePanel.vue'
import zh from '@/i18n/locales/zh'
import { checkTask, fetchTask, fetchTasks } from '@/api/taskQueue'
import { onlineAction, type OnlineJob } from '@/api/onlineProducts'
import type { BackendQueueTask } from '@/types/workflow.generated'

vi.mock('@/api/taskQueue', () => ({ checkTask: vi.fn(), fetchTask: vi.fn(), fetchTasks: vi.fn() }))
vi.mock('@/api/onlineProducts', () => ({ onlineAction: vi.fn() }))
const task: BackendQueueTask = {
  id: 'publish:job-1:ozon', job_id: 'job-1', source: 'publish', origin: 'page', platform: 'ozon',
  account_id: 'store-1', operation: 'publish', title: '测试商品', target_id: 'product-1',
  status: 'waiting', business_status: 'pending_confirmation', created_at: '2026-10-30T10:00:00Z', updated_at: '2026-10-30T10:01:00Z',
  error: '', protection: '禁止重复发布', can_retry: false, confirmation: {
    state: 'waiting', checks_used: 1, checks_remaining: 9, requests_used: 2, requests_remaining: 78,
    last_checked_at: Date.now() / 1000 - 60, next_check_at: Date.now() / 1000 + 60, deadline_at: Date.now() / 1000 + 900, error: '',
  },
}
const detail = { ok: true, task, detail: { status: 'pending_confirmation', result: { task_id: 'remote-1' } } }
function render(props: { initialTaskId?: string; initialSource?: string } = {}) {
  return mount(TaskQueuePanel, { props, global: { plugins: [createI18n({ legacy: false, locale: 'zh', messages: { zh } })], stubs: { OnlineOperationRecovery: true } } })
}
let wrapper: ReturnType<typeof render>
beforeEach(() => {
  vi.resetAllMocks()
  vi.useFakeTimers()
  Object.defineProperty(document, 'hidden', { configurable: true, value: false })
  vi.mocked(fetchTasks).mockResolvedValue({ ok: true, items: [task], total: 1, page: 1, accounts: ['store-1'] })
  vi.mocked(fetchTask).mockResolvedValue(detail)
})
afterEach(() => { wrapper?.unmount(); vi.clearAllMocks(); vi.useRealTimers() })

describe('统一任务队列', () => {
  it('页面轮询只读本地任务，筛选来源和状态发送给列表接口', async () => {
    wrapper = render()
    await flushPromises()
    expect(wrapper.text()).toContain('等待结果')
    await wrapper.get('select[aria-label="业务来源"]').setValue('online')
    await wrapper.get('select[aria-label="状态"]').setValue('needs_attention')
    await vi.advanceTimersByTimeAsync(250)
    await flushPromises()
    expect(fetchTasks).toHaveBeenLastCalledWith(expect.objectContaining({ source: 'online', status: 'needs_attention', page: 1 }))
    await vi.advanceTimersByTimeAsync(5000)
    await flushPromises()
    expect(checkTask).not.toHaveBeenCalled()
  })

  it('显式查询更新原任务详情并展示查询计划', async () => {
    wrapper = render()
    await flushPromises()
    await wrapper.findAll('button').find(button => button.text() === '测试商品')!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('自动查询剩余轮数')
    expect(wrapper.text()).toContain('自动确认截止时间')
    const completed = { ...detail, task: { ...task, status: 'completed', business_status: 'success' } }
    vi.mocked(checkTask).mockResolvedValue({ ...completed, checked: true, resolution: 'applied' })
    vi.mocked(fetchTask).mockResolvedValue(completed)
    await wrapper.findAll('button').find(button => button.text() === '查询平台结果')!.trigger('click')
    await flushPromises()
    expect(checkTask).toHaveBeenCalledOnce()
    expect(checkTask).toHaveBeenCalledWith(task.id)
    expect(wrapper.text()).toContain('已完成')
    expect(wrapper.text()).toContain('已查询，任务结果已更新')
  })

  it('显示需要处理和已结束未确认，保留在线修改的人工核对入口', async () => {
    const online = { ...task, id: 'online:change-1', job_id: 'change-1', source: 'online' as const, operation: 'price',
      status: 'needs_attention', business_status: 'needs_attention', confirmation: { ...task.confirmation!, state: 'needs_attention', error: '价格隔离', next_check_at: 0 } }
    vi.mocked(fetchTasks).mockResolvedValue({ ok: true, items: [online, { ...task, id: 'closed', title: '旧修改', status: 'closed_unconfirmed' }], total: 2, page: 1, accounts: ['store-1'] })
    vi.mocked(fetchTask).mockResolvedValue({ ok: true, task: online, detail: { id: 'change-1', status: 'needs_attention', request: {}, result: {} } })
    wrapper = render()
    await flushPromises()
    expect(wrapper.text()).toContain('已结束，未确认')
    await wrapper.findAll('button').find(button => button.text() === '测试商品')!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('价格隔离')
    expect(wrapper.find('online-operation-recovery-stub').exists()).toBe(true)
    expect(wrapper.text()).not.toContain('仅重试失败项')
    expect(checkTask).not.toHaveBeenCalled()
  })

  it('从在线商品入口直接选中当前列表之外的任务，只读取本地详情', async () => {
    const online: BackendQueueTask = { ...task, id: 'online:change-1', job_id: 'change-1', source: 'online', operation: 'price' }
    vi.mocked(fetchTasks).mockResolvedValue({ ok: true, items: [], total: 0, page: 1, accounts: [] })
    vi.mocked(fetchTask).mockResolvedValue({ ok: true, task: online, detail: { id: 'change-1', operation: 'price', request: {}, result: {} } })
    wrapper = render({ initialTaskId: online.id, initialSource: 'online' })
    await flushPromises()
    expect(fetchTasks).toHaveBeenCalledWith(expect.objectContaining({ source: 'online' }))
    expect(fetchTask).toHaveBeenCalledWith(online.id)
    expect(wrapper.get('aside').text()).toContain('测试商品')
    expect(checkTask).not.toHaveBeenCalled()
    expect(onlineAction).not.toHaveBeenCalled()
  })

  it('同步任务展示进度与失败项，显式重试后选中新任务', async () => {
    const sync: BackendQueueTask = { ...task, id: 'online:sync-1', job_id: 'sync-1', source: 'online',
      operation: 'sync', title: '同步店铺商品', target_id: '*', status: 'partial', business_status: 'partial',
      protection: '', confirmation: null, can_retry: true }
    const syncJob: OnlineJob = { id: 'sync-1', operation: 'sync', platform: 'mercadolibre', status: 'partial',
      created_at: '', updated_at: '', target_id: '*', request: {},
      result: { completed: 2, failed: 1, discovered: 3, items: [{ remote_id: 'SKU-3', status: 'failed', error: '商品详情读取失败' }] } }
    const retried: BackendQueueTask = { ...sync, id: 'online:sync-2', job_id: 'sync-2', status: 'queued', business_status: 'queued', can_retry: false }
    const newJob: OnlineJob = { ...syncJob, id: 'sync-2', status: 'queued', result: {} }
    vi.mocked(fetchTasks).mockResolvedValue({ ok: true, items: [sync], total: 1, page: 1, accounts: [] })
    vi.mocked(fetchTask).mockImplementation(async id => ({ ok: true, task: id === sync.id ? sync : retried, detail: { ...(id === sync.id ? syncJob : newJob) } }))
    vi.mocked(onlineAction).mockResolvedValue(newJob)
    wrapper = render({ initialTaskId: sync.id })
    await flushPromises()
    expect(wrapper.get('aside').text()).toContain('已发现 3 件 · 成功 2 件 · 失败 1 件')
    expect(wrapper.get('aside').text()).toContain('SKU-3：商品详情读取失败')
    expect(wrapper.find('online-operation-recovery-stub').exists()).toBe(false)
    expect(wrapper.get('aside').text()).not.toContain('查询平台结果')
    expect(onlineAction).not.toHaveBeenCalled()
    await wrapper.findAll('button').find(button => button.text() === '仅重试失败项')!.trigger('click')
    await flushPromises()
    expect(onlineAction).toHaveBeenCalledWith('retry', { job_id: 'sync-1', idempotency_key: expect.any(String) })
    expect(fetchTask).toHaveBeenLastCalledWith(retried.id)
    expect(wrapper.get('aside').text()).toContain('sync-2')
    expect(wrapper.text()).toContain('重试任务已入队')
    expect(checkTask).not.toHaveBeenCalled()
  })

  it('自动预算耗尽后仍允许显式人工查询，页面轮询不重新启动查询', async () => {
    const expired = { ...task, status: 'needs_attention', confirmation: { ...task.confirmation!,
      state: 'needs_attention', checks_remaining: 0, requests_remaining: 0, deadline_at: Date.now() / 1000 - 60, next_check_at: 0 } }
    const expiredDetail = { ...detail, task: expired }
    vi.mocked(fetchTasks).mockResolvedValue({ ok: true, items: [expired], total: 1, page: 1, accounts: ['store-1'] })
    vi.mocked(fetchTask).mockResolvedValue(expiredDetail)
    vi.mocked(checkTask).mockResolvedValue({ ...expiredDetail, checked: true, resolution: 'pending' })
    wrapper = render()
    await flushPromises()
    await wrapper.findAll('button').find(button => button.text() === '测试商品')!.trigger('click')
    await flushPromises()
    const query = wrapper.findAll('button').find(button => button.text() === '查询平台结果')
    expect(query).toBeDefined()
    await query!.trigger('click')
    await flushPromises()
    expect(checkTask).toHaveBeenCalledOnce()
    await vi.advanceTimersByTimeAsync(5000)
    await flushPromises()
    expect(checkTask).toHaveBeenCalledOnce()
  })
})
