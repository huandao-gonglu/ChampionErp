import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  approveGlobalTask,
  cancelGlobalTask,
  fetchGlobalTask,
  rejectGlobalTask,
  submitGlobalTaskInput,
} from '@/api/globalTasks'
import GlobalTaskApprovalCard from '../GlobalTaskApprovalCard.vue'

vi.mock('@/api/globalTasks', () => ({
  approveGlobalTask: vi.fn(),
  cancelGlobalTask: vi.fn(),
  fetchGlobalTask: vi.fn(),
  rejectGlobalTask: vi.fn(),
  submitGlobalTaskInput: vi.fn(),
}))

function taskResponse(overrides: Record<string, unknown> = {}) {
  return {
    ok: true as const,
    task_id: 'gtask-1',
    task: {
      task_id: 'gtask-1',
      goal: '删除指定商品',
      status: 'in_progress',
      steps: [{ step_id: 'step-1', capability_name: 'product_delete', status: 'running' }],
      current_step_index: 0,
      ...overrides,
    },
  }
}

function mountCard(options: { enabled?: boolean } = {}) {
  return mount(GlobalTaskApprovalCard, {
    props: { taskId: 'gtask-1', enabled: options.enabled ?? true },
  })
}

describe('GlobalTaskApprovalCard 只读任务卡', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(fetchGlobalTask).mockResolvedValue(taskResponse() as never)
  })

  it('挂载与刷新只走纯 GET，不触发任何写接口', async () => {
    const wrapper = mountCard()
    await flushPromises()

    expect(fetchGlobalTask).toHaveBeenCalledWith('gtask-1')
    expect(wrapper.get('[data-testid="global-task-card"]').text()).toContain('后台任务执行中')

    await wrapper.get('[data-testid="global-task-refresh"]').trigger('click')
    await flushPromises()

    expect(fetchGlobalTask).toHaveBeenCalledTimes(2)
    expect(approveGlobalTask).not.toHaveBeenCalled()
    expect(rejectGlobalTask).not.toHaveBeenCalled()
    expect(submitGlobalTaskInput).not.toHaveBeenCalled()
    expect(cancelGlobalTask).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('待审批状态展示摘要并支持批准', async () => {
    vi.mocked(fetchGlobalTask).mockResolvedValue(taskResponse({
      status: 'pending_approval',
      pending_approval: {
        step_id: 'step-1',
        capability_name: 'product_delete',
        capability_version: '1',
        task_revision: 2,
        digest: 'digest-1',
        requested_at: '2026-08-19T12:00:00Z',
        payload: {
          summary: '删除 2 个本地商品：product-1、product-2',
          canonical_payload: { product_ids: ['product-1', 'product-2'] },
        },
      },
    }) as never)
    vi.mocked(approveGlobalTask).mockResolvedValue(taskResponse({
      status: 'completed',
      pending_approval: null,
      assistant_message: '任务已完成。',
    }) as never)
    vi.spyOn(window, 'confirm').mockReturnValue(true)

    const wrapper = mountCard()
    await flushPromises()

    const approval = wrapper.get('[data-testid="global-task-approval"]')
    expect(approval.text()).toContain('删除 2 个本地商品')

    await wrapper.get('[data-testid="global-task-approve"]').trigger('click')
    await flushPromises()

    expect(approveGlobalTask).toHaveBeenCalledWith('gtask-1', 'step-1')
    expect(wrapper.get('[data-testid="global-task-card"]').text()).toContain('已完成')
    wrapper.unmount()
  })

  it('拒绝必须填写原因并提交给后端', async () => {
    vi.mocked(fetchGlobalTask).mockResolvedValue(taskResponse({
      status: 'pending_approval',
      pending_approval: {
        step_id: 'step-1',
        capability_name: 'product_delete',
        capability_version: '1',
        task_revision: 2,
        digest: 'digest-1',
        requested_at: '2026-08-19T12:00:00Z',
        payload: { summary: '删除商品' },
      },
    }) as never)
    vi.mocked(rejectGlobalTask).mockResolvedValue(taskResponse({ status: 'cancelled' }) as never)

    const wrapper = mountCard()
    await flushPromises()

    const rejectButton = wrapper.get('[data-testid="global-task-reject"]')
    expect((rejectButton.element as HTMLButtonElement).disabled).toBe(true)

    await wrapper.get('[data-testid="global-task-reject-reason"]').setValue('目标不正确')
    await wrapper.get('[data-testid="global-task-reject"]').trigger('click')
    await flushPromises()

    expect(rejectGlobalTask).toHaveBeenCalledWith('gtask-1', 'step-1', '目标不正确')
    expect(wrapper.get('[data-testid="global-task-card"]').text()).toContain('已取消')
    wrapper.unmount()
  })

  it('待补资料状态提交补充字段', async () => {
    vi.mocked(fetchGlobalTask).mockResolvedValue(taskResponse({
      status: 'needs_input',
      pending_inputs: [
        { key: 'category_name', label: '类目名称', reason: '未找到匹配类目' },
      ],
    }) as never)
    vi.mocked(submitGlobalTaskInput).mockResolvedValue(taskResponse({ status: 'in_progress' }) as never)

    const wrapper = mountCard()
    await flushPromises()

    await wrapper.get('[data-testid="global-task-input-category_name"]').setValue('连衣裙')
    await wrapper.get('[data-testid="global-task-input-submit"]').trigger('click')
    await flushPromises()

    expect(submitGlobalTaskInput).toHaveBeenCalledWith('gtask-1', { category_name: '连衣裙' })
    wrapper.unmount()
  })

  it('select 待补字段渲染选项下拉并提交所选字符串（A-08）', async () => {
    vi.mocked(fetchGlobalTask).mockResolvedValue(taskResponse({
      status: 'needs_input',
      pending_inputs: [
        {
          key: 'target_platform',
          label: '目标平台',
          reason: '请确认发布平台',
          input_type: 'select',
          options: ['mercadolibre', 'amazon'],
        },
      ],
    }) as never)
    vi.mocked(submitGlobalTaskInput).mockResolvedValue(taskResponse({ status: 'in_progress' }) as never)

    const wrapper = mountCard()
    await flushPromises()

    const select = wrapper.get('[data-testid="global-task-input-target_platform"]')
    expect(select.element.tagName).toBe('SELECT')
    const optionValues = wrapper.findAll('option').map((option) => option.element.value)
    expect(optionValues).toContain('mercadolibre')
    expect(optionValues).toContain('amazon')

    await select.setValue('mercadolibre')
    await wrapper.get('[data-testid="global-task-input-submit"]').trigger('click')
    await flushPromises()

    expect(submitGlobalTaskInput).toHaveBeenCalledWith(
      'gtask-1',
      { target_platform: 'mercadolibre' },
    )
    wrapper.unmount()
  })

  it('string_list 待补字段按换行/逗号拆成字符串数组提交（A-08）', async () => {
    vi.mocked(fetchGlobalTask).mockResolvedValue(taskResponse({
      status: 'needs_input',
      pending_inputs: [
        {
          key: 'asset_ids',
          label: '图片资源 ID',
          reason: '需要商品图片',
          input_type: 'string_list',
        },
      ],
    }) as never)
    vi.mocked(submitGlobalTaskInput).mockResolvedValue(taskResponse({ status: 'in_progress' }) as never)

    const wrapper = mountCard()
    await flushPromises()

    const textarea = wrapper.get('[data-testid="global-task-input-asset_ids"]')
    expect(textarea.element.tagName).toBe('TEXTAREA')

    await textarea.setValue('asset-1, asset-2\nasset-3')
    await wrapper.get('[data-testid="global-task-input-submit"]').trigger('click')
    await flushPromises()

    // 提交的是字符串数组，而不是原始字符串。
    expect(submitGlobalTaskInput).toHaveBeenCalledWith(
      'gtask-1',
      { asset_ids: ['asset-1', 'asset-2', 'asset-3'] },
    )
    wrapper.unmount()
  })

  it('json_object 待补字段解析成对象提交（A-08）', async () => {
    vi.mocked(fetchGlobalTask).mockResolvedValue(taskResponse({
      status: 'needs_input',
      pending_inputs: [
        {
          key: 'attributes',
          label: '商品属性',
          reason: '结构化属性',
          input_type: 'json_object',
        },
      ],
    }) as never)
    vi.mocked(submitGlobalTaskInput).mockResolvedValue(taskResponse({ status: 'in_progress' }) as never)

    const wrapper = mountCard()
    await flushPromises()

    const textarea = wrapper.get('[data-testid="global-task-input-attributes"]')
    expect(textarea.element.tagName).toBe('TEXTAREA')

    await textarea.setValue('{"color": "red", "size": "M"}')
    await wrapper.get('[data-testid="global-task-input-submit"]').trigger('click')
    await flushPromises()

    expect(submitGlobalTaskInput).toHaveBeenCalledWith(
      'gtask-1',
      { attributes: { color: 'red', size: 'M' } },
    )
    wrapper.unmount()
  })

  it('非法 JSON / 非对象 json_object 在提交前被拦截（A-08）', async () => {
    vi.mocked(fetchGlobalTask).mockResolvedValue(taskResponse({
      status: 'needs_input',
      pending_inputs: [
        { key: 'attributes', label: '商品属性', input_type: 'json_object' },
      ],
    }) as never)

    const wrapper = mountCard()
    await flushPromises()
    const textarea = wrapper.get('[data-testid="global-task-input-attributes"]')

    // 非法 JSON：拦截且不提交。
    await textarea.setValue('not-json')
    await wrapper.get('[data-testid="global-task-input-submit"]').trigger('click')
    await flushPromises()
    expect(submitGlobalTaskInput).not.toHaveBeenCalled()
    expect(wrapper.get('[data-testid="global-task-action-error"]').text())
      .toContain('不是合法 JSON')

    // JSON 数组不是对象：同样拦截。
    await textarea.setValue('[1, 2]')
    await wrapper.get('[data-testid="global-task-input-submit"]').trigger('click')
    await flushPromises()
    expect(submitGlobalTaskInput).not.toHaveBeenCalled()
    expect(wrapper.get('[data-testid="global-task-action-error"]').text())
      .toContain('必须是 JSON 对象')
    wrapper.unmount()
  })

  it('执行中且当前步骤未运行时可取消任务', async () => {
    vi.mocked(fetchGlobalTask).mockResolvedValue(taskResponse({ status: 'running' }) as never)
    vi.mocked(cancelGlobalTask).mockResolvedValue(taskResponse({ status: 'cancelled' }) as never)
    vi.spyOn(window, 'confirm').mockReturnValue(true)

    const wrapper = mountCard()
    await flushPromises()

    await wrapper.get('[data-testid="global-task-cancel"]').trigger('click')
    await flushPromises()

    expect(cancelGlobalTask).toHaveBeenCalledWith('gtask-1')
    expect(wrapper.get('[data-testid="global-task-card"]').text()).toContain('已取消')
    wrapper.unmount()
  })

  it('待审批状态也允许取消任务（与服务端状态机一致）', async () => {
    vi.mocked(fetchGlobalTask).mockResolvedValue(taskResponse({
      status: 'pending_approval',
      pending_approval: {
        step_id: 'step-1',
        capability_name: 'product_delete',
        capability_version: '1',
        task_revision: 2,
        digest: 'digest-1',
        requested_at: '2026-08-19T12:00:00Z',
        payload: { summary: '删除商品' },
      },
    }) as never)
    vi.mocked(cancelGlobalTask).mockResolvedValue(taskResponse({ status: 'cancelled' }) as never)
    vi.spyOn(window, 'confirm').mockReturnValue(true)

    const wrapper = mountCard()
    await flushPromises()

    await wrapper.get('[data-testid="global-task-cancel"]').trigger('click')
    await flushPromises()

    expect(cancelGlobalTask).toHaveBeenCalledWith('gtask-1')
    expect(wrapper.get('[data-testid="global-task-card"]').text()).toContain('已取消')
    wrapper.unmount()
  })

  it('in_progress 已提交外部系统：不展示取消按钮', async () => {
    const wrapper = mountCard()
    await flushPromises()

    expect(wrapper.get('[data-testid="global-task-card"]').text()).toContain('后台任务执行中')
    expect(wrapper.find('[data-testid="global-task-cancel"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('操作失败时展示错误但不破坏任务卡', async () => {
    vi.mocked(fetchGlobalTask).mockResolvedValue(taskResponse({ status: 'running' }) as never)
    vi.mocked(cancelGlobalTask).mockRejectedValue(new Error('任务已进入终态'))
    vi.spyOn(window, 'confirm').mockReturnValue(true)

    const wrapper = mountCard()
    await flushPromises()

    await wrapper.get('[data-testid="global-task-cancel"]').trigger('click')
    await flushPromises()

    expect(wrapper.get('[data-testid="global-task-action-error"]').text()).toContain('任务已进入终态')
    expect(wrapper.find('[data-testid="global-task-card"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('只读模式展示状态但不提供任何操作按钮', async () => {
    vi.mocked(fetchGlobalTask).mockResolvedValue(taskResponse({
      status: 'pending_approval',
      pending_approval: {
        step_id: 'step-1',
        capability_name: 'product_delete',
        capability_version: '1',
        task_revision: 2,
        digest: 'digest-1',
        requested_at: '2026-08-19T12:00:00Z',
        payload: { summary: '删除商品' },
      },
    }) as never)

    const wrapper = mountCard({ enabled: false })
    await flushPromises()

    expect(wrapper.find('[data-testid="global-task-card"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('只读消息不能审批')
    expect(wrapper.find('[data-testid="global-task-approve"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="global-task-refresh"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="global-task-cancel"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('taskId 切换后旧任务的慢响应不得写入新任务卡（A-10）', async () => {
    let resolveStale!: (value: unknown) => void
    const stalePromise = new Promise((resolve) => {
      resolveStale = resolve
    })
    vi.mocked(fetchGlobalTask).mockImplementation(((taskId: string) => {
      if (taskId === 'gtask-1') return stalePromise
      return Promise.resolve(taskResponse({ task_id: 'gtask-2' }))
    }) as never)

    const wrapper = mount(GlobalTaskApprovalCard, {
      props: { taskId: 'gtask-1', enabled: true },
    })
    await flushPromises()
    // gtask-1 的首次读取仍在途（stalePromise 未 resolve）。

    // 切换到 gtask-2：快速返回 gtask-2 状态并渲染。
    await wrapper.setProps({ taskId: 'gtask-2' })
    await flushPromises()
    expect(wrapper.text()).toContain('gtask-2')

    // 此时 gtask-1 的慢响应才到达：不得覆盖已切换的 gtask-2 任务卡。
    resolveStale(taskResponse({ task_id: 'gtask-1' }))
    await flushPromises()
    expect(wrapper.text()).toContain('gtask-2')
    expect(wrapper.text()).not.toContain('gtask-1')
    wrapper.unmount()
  })

  it('写操作响应优先于在途轮询只读响应（A-10）', async () => {
    vi.useFakeTimers()
    let resolvePendingRead!: (value: unknown) => void
    const pendingReadPromise = new Promise((resolve) => {
      resolvePendingRead = resolve
    })
    let readCalls = 0
    vi.mocked(fetchGlobalTask).mockImplementation((() => {
      readCalls += 1
      if (readCalls === 1) return Promise.resolve(taskResponse({ status: 'running' }))
      return pendingReadPromise
    }) as never)
    vi.mocked(cancelGlobalTask).mockResolvedValue(
      taskResponse({ status: 'cancelled' }) as never,
    )
    vi.spyOn(window, 'confirm').mockReturnValue(true)

    const wrapper = mountCard()
    await vi.advanceTimersByTimeAsync(0)
    expect(wrapper.text()).toContain('执行中')

    // 推进 4 秒触发一次轮询只读读取；它 showBusy=false、不占用 busyAction，
    // 因此不会阻塞用户取消。此时该读取在途（pendingReadPromise 未决）。
    await vi.advanceTimersByTimeAsync(4000)

    // 用户取消任务：写响应把状态推进到 cancelled 并递增代次。
    await wrapper.get('[data-testid="global-task-cancel"]').trigger('click')
    await vi.advanceTimersByTimeAsync(0)
    expect(wrapper.text()).toContain('已取消')

    // 在途的旧轮询读取此时才返回 running：不得覆盖取消后的终态。
    resolvePendingRead(taskResponse({ status: 'running' }))
    await vi.advanceTimersByTimeAsync(0)
    expect(wrapper.text()).toContain('已取消')

    wrapper.unmount()
    vi.useRealTimers()
  })

  it('任务 A 的慢写响应不得覆盖已切换到的任务 B（A-10）', async () => {
    let resolveSlowCancel!: (value: unknown) => void
    const slowCancelPromise = new Promise((resolve) => {
      resolveSlowCancel = resolve
    })
    vi.mocked(fetchGlobalTask).mockImplementation(((taskId: string) => {
      if (taskId === 'gtask-1') {
        return Promise.resolve(
          taskResponse({ task_id: 'gtask-1', status: 'running' }),
        )
      }
      return Promise.resolve(
        taskResponse({ task_id: 'gtask-2', status: 'running' }),
      )
    }) as never)
    vi.mocked(cancelGlobalTask).mockImplementation(
      () => slowCancelPromise as never,
    )
    vi.spyOn(window, 'confirm').mockReturnValue(true)

    const wrapper = mount(GlobalTaskApprovalCard, {
      props: { taskId: 'gtask-1', enabled: true },
    })
    await flushPromises()
    expect(wrapper.text()).toContain('gtask-1')

    // 对任务 A 发起取消；写请求在途未决。
    await wrapper.get('[data-testid="global-task-cancel"]').trigger('click')
    await flushPromises()
    expect(cancelGlobalTask).toHaveBeenCalledWith('gtask-1')

    // 在途期间切换到任务 B：B 加载完成并渲染。
    await wrapper.setProps({ taskId: 'gtask-2' })
    await flushPromises()
    expect(wrapper.text()).toContain('gtask-2')

    // A 的慢取消响应此时才到达：不得覆盖已切换的任务 B。
    resolveSlowCancel(
      taskResponse({ task_id: 'gtask-1', status: 'cancelled' }),
    )
    await flushPromises()
    expect(wrapper.text()).toContain('gtask-2')
    expect(wrapper.text()).not.toContain('gtask-1')
    wrapper.unmount()
  })

  it('任务 A 的旧成功写响应不得作废任务 B 的在途读取（A-10）', async () => {
    let resolveSlowCancel!: (value: unknown) => void
    const slowCancelPromise = new Promise((resolve) => {
      resolveSlowCancel = resolve
    })
    let resolveBRead!: (value: unknown) => void
    const bReadPromise = new Promise((resolve) => {
      resolveBRead = resolve
    })
    vi.mocked(fetchGlobalTask).mockImplementation(((taskId: string) => {
      if (taskId === 'gtask-1') {
        return Promise.resolve(
          taskResponse({ task_id: 'gtask-1', status: 'running' }),
        )
      }
      return bReadPromise as Promise<never>
    }) as never)
    vi.mocked(cancelGlobalTask).mockImplementation(
      () => slowCancelPromise as never,
    )
    vi.spyOn(window, 'confirm').mockReturnValue(true)

    const wrapper = mount(GlobalTaskApprovalCard, {
      props: { taskId: 'gtask-1', enabled: true },
    })
    await flushPromises()
    await wrapper.get('[data-testid="global-task-cancel"]').trigger('click')
    await flushPromises()

    // 切换到任务 B：B 的 GET 在途未决。
    await wrapper.setProps({ taskId: 'gtask-2' })
    await flushPromises()

    // A 的慢成功响应此时到达：先核对 taskId，不得递增代次作废 B 的在途读取。
    resolveSlowCancel(
      taskResponse({ task_id: 'gtask-1', status: 'cancelled' }),
    )
    await flushPromises()

    // B 的读取随后完成：必须能正常应用。
    resolveBRead(taskResponse({ task_id: 'gtask-2', status: 'running' }))
    await flushPromises()
    expect(wrapper.text()).toContain('gtask-2')
    expect(wrapper.text()).not.toContain('gtask-1')
    wrapper.unmount()
  })

  it('任务 A 的失败写响应不得显示在已切换的任务 B 卡片上（A-10）', async () => {
    let rejectSlowCancel!: (reason: unknown) => void
    const slowCancelPromise = new Promise((_, reject) => {
      rejectSlowCancel = reject
    })
    vi.mocked(fetchGlobalTask).mockImplementation(((taskId: string) => (
      Promise.resolve(taskResponse({ task_id: taskId, status: 'running' }))
    )) as never)
    vi.mocked(cancelGlobalTask).mockImplementation(
      () => slowCancelPromise as never,
    )
    vi.spyOn(window, 'confirm').mockReturnValue(true)

    const wrapper = mount(GlobalTaskApprovalCard, {
      props: { taskId: 'gtask-1', enabled: true },
    })
    await flushPromises()
    await wrapper.get('[data-testid="global-task-cancel"]').trigger('click')
    await flushPromises()

    // 切换到任务 B 并完成加载。
    await wrapper.setProps({ taskId: 'gtask-2' })
    await flushPromises()
    expect(wrapper.text()).toContain('gtask-2')

    // A 的失败响应此时才到达：错误不得显示在 B 卡片上。
    rejectSlowCancel(new Error('取消失败'))
    await flushPromises()
    expect(wrapper.find('[data-testid="global-task-action-error"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('取消失败')
    // B 的按钮不被 A 的在途操作禁用（taskId 切换清除 busy）。
    const cancelButton = wrapper.get('[data-testid="global-task-cancel"]')
    expect((cancelButton.element as HTMLButtonElement).disabled).toBe(false)
    wrapper.unmount()
  })
})
