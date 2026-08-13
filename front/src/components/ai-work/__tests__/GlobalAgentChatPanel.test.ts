import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import GlobalAgentChatPanel from '../GlobalAgentChatPanel.vue'
import type { AiWorkEvent } from '@/types/aiWork'
import type { GlobalTaskResponse, GlobalTaskState } from '@/types/globalTasks'

const mocks = vi.hoisted(() => ({
  start: vi.fn(),
  state: vi.fn(),
  input: vi.fn(),
  confirm: vi.fn(),
  cancel: vi.fn(),
}))

vi.mock('@/api/globalTasks', () => ({
  startGlobalTask: mocks.start,
  fetchGlobalTaskState: mocks.state,
  submitGlobalTaskInput: mocks.input,
  confirmGlobalTaskPublish: mocks.confirm,
  cancelGlobalTask: mocks.cancel,
}))

function task(
  status: GlobalTaskState['status'],
  overrides: Partial<GlobalTaskState> = {},
): GlobalTaskState {
  return {
    schema_version: 1,
    task_id: 'task-1',
    task_kind: 'global.agent.chat',
    goal: '把第二个草稿发布到 Ozon',
    product_id: '',
    platform: 'ozon',
    status,
    steps: [],
    current_step_index: 0,
    pending_inputs: [],
    pending_input_owner: status === 'needs_input' ? 'capability' : 'none',
    publish_confirmation: {
      status: 'none',
      validation_digest: '',
      summary: {},
      confirmed_at: null,
    },
    publish_idempotency_key: '',
    publish_job_id: '',
    draft_query_snapshot_id: '',
    ai_work_conversation_id: 'conversation-1',
    agent_execution_conversation_ids: [],
    assistant_message: '',
    plan_explanation: '',
    error_code: '',
    error_message: '',
    created_at: '2026-08-13T10:00:00+08:00',
    updated_at: '2026-08-13T10:00:00+08:00',
    ...overrides,
  }
}

function response(value: GlobalTaskState): GlobalTaskResponse {
  return {
    ok: true,
    task: value,
    task_id: value.task_id,
    ai_work_conversation_id: value.ai_work_conversation_id,
  }
}

function taskProjection(taskId = 'task-1'): AiWorkEvent {
  return {
    schema_version: 1,
    seq: 1,
    timestamp: 1,
    occurred_at: '2026-08-13T10:00:00+08:00',
    type: 'CUSTOM',
    threadId: 'conversation-1',
    runId: taskId,
    conversation_id: 'conversation-1',
    name: 'global.task_state',
    value: { task_id: taskId, status: 'running' },
  }
}

function executionSummary(
  conversationId: string,
  useCaseId: string,
): import('@/types/aiWork').AiWorkConversationSummary {
  return {
    conversation_id: conversationId,
    parent_conversation_id: 'conversation-1',
    use_case_id: useCaseId,
    capability: 'agent',
    provider_id: 'deepseek',
    provider: 'DeepSeek',
    model_id: 'deepseek-v4-pro',
    model: 'deepseek-v4-pro',
    stream: false,
    required_capabilities: [],
    timeout_seconds: 60,
    status: 'completed',
    created_at: '2026-08-13T10:00:00+08:00',
    updated_at: '2026-08-13T10:00:05+08:00',
    last_seq: 2,
    event_count: 2,
    error: '',
  }
}

describe('GlobalAgentChatPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.useRealTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('空白面板不会创建记录，首次发送才启动任务', async () => {
    mocks.start.mockResolvedValue(response(task('running')))
    const wrapper = mount(GlobalAgentChatPanel)

    expect(mocks.start).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('告诉全局 Agent 你想完成什么')

    await wrapper.get('#global-agent-goal').setValue('把第二个草稿发布到 Ozon')
    await wrapper.get('[data-testid="global-goal-composer"]').trigger('submit')
    await flushPromises()

    expect(mocks.start).toHaveBeenCalledWith({
      goal: '把第二个草稿发布到 Ozon',
      task_kind: 'global.agent.chat',
    })
    expect(wrapper.emitted('conversation-created')?.[0]).toEqual([{
      conversationId: 'conversation-1',
      taskId: 'task-1',
    }])
    expect(wrapper.text()).toContain('把第二个草稿发布到 Ozon')
    wrapper.unmount()
  })

  it('needs_input 只把字段化资料提交给 input 端点', async () => {
    const needsInput = task('needs_input', {
      pending_inputs: [{
        key: 'battery_type',
        label: '电池类型',
        reason: 'Ozon 发布必填',
        input_type: 'select',
        options: ['锂电池', '干电池'],
        input_owner: 'step',
      }],
    })
    mocks.state.mockResolvedValue(response(needsInput))
    mocks.input.mockResolvedValue(response(task('running')))

    const wrapper = mount(GlobalAgentChatPanel, {
      props: {
        conversationId: 'conversation-1',
        events: [taskProjection()],
      },
    })
    await flushPromises()

    expect(wrapper.find('[data-testid="global-goal-composer"]').exists()).toBe(false)
    await wrapper.get('#global-input-battery_type').setValue('锂电池')
    await wrapper.get('[data-testid="submit-global-input"]').trigger('click')
    await flushPromises()

    expect(mocks.input).toHaveBeenCalledWith({
      task_id: 'task-1',
      message: '电池类型：锂电池',
      inputs: { battery_type: '锂电池' },
    })
    expect(mocks.start).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('零步骤终态不显示仍在规划，并在最新任务加载完成前禁止新目标', async () => {
    let resolveState: ((value: GlobalTaskResponse) => void) | undefined
    mocks.state.mockImplementation(() => new Promise<GlobalTaskResponse>((resolve) => {
      resolveState = resolve
    }))
    const wrapper = mount(GlobalAgentChatPanel, {
      props: {
        conversationId: 'conversation-1',
        events: [taskProjection('task-2')],
      },
    })

    expect(wrapper.find('[data-testid="global-goal-composer"]').exists()).toBe(false)
    resolveState?.(response(task('completed', { task_id: 'task-2' })))
    await flushPromises()

    expect(wrapper.text()).toContain('无需执行业务步骤')
    expect(wrapper.text()).not.toContain('正在生成执行计划')
    expect(wrapper.find('[data-testid="global-goal-composer"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('发布确认只能通过独立按钮提交', async () => {
    const waiting = task('waiting_publish_confirmation', {
      publish_confirmation: {
        status: 'pending',
        validation_digest: 'digest-1',
        summary: { platform: 'Ozon', price: 199 },
        confirmed_at: null,
      },
    })
    mocks.state.mockResolvedValue(response(waiting))
    mocks.confirm.mockResolvedValue(response(task('waiting_publish_result', {
      publish_job_id: 'job-1',
    })))

    const wrapper = mount(GlobalAgentChatPanel, {
      props: { conversationId: 'conversation-1', events: [taskProjection()] },
    })
    await flushPromises()

    expect(wrapper.find('[data-testid="global-goal-composer"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('发送文字不会触发发布')
    await wrapper.get('[data-testid="confirm-global-publish"]').trigger('click')
    await flushPromises()

    expect(mocks.confirm).toHaveBeenCalledOnce()
    expect(mocks.confirm).toHaveBeenCalledWith('task-1')
    expect(mocks.input).not.toHaveBeenCalled()
    expect(mocks.start).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('waiting_publish_result 有限频率读取状态，终态后恢复同会话输入框', async () => {
    vi.useFakeTimers()
    const waiting = task('waiting_publish_result', {
      publish_job_id: 'job-1',
      draft_query_snapshot_id: 'snapshot-1',
    })
    mocks.state
      .mockResolvedValueOnce(response(waiting))
      .mockResolvedValueOnce(response(task('completed', {
        draft_query_snapshot_id: 'snapshot-1',
      })))
    mocks.start.mockResolvedValue(response(task('running', { task_id: 'task-2' })))

    const wrapper = mount(GlobalAgentChatPanel, {
      props: { conversationId: 'conversation-1', events: [taskProjection()] },
    })
    await flushPromises()

    expect(wrapper.get('[data-testid="publish-result-waiting"]').text()).toContain('job-1')
    expect(mocks.state).toHaveBeenCalledTimes(1)

    await vi.advanceTimersByTimeAsync(2_499)
    expect(mocks.state).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(1)
    await flushPromises()

    expect(mocks.state).toHaveBeenCalledTimes(2)
    expect(wrapper.find('[data-testid="publish-result-waiting"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="global-goal-composer"]').exists()).toBe(true)

    await wrapper.get('#global-agent-goal').setValue('继续检查第一个草稿')
    await wrapper.get('[data-testid="global-goal-composer"]').trigger('submit')
    await flushPromises()

    expect(mocks.start).toHaveBeenCalledWith({
      goal: '继续检查第一个草稿',
      task_kind: 'global.agent.chat',
      ai_work_conversation_id: 'conversation-1',
      draft_query_snapshot_id: 'snapshot-1',
    })
    expect(mocks.confirm).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('执行详情合并投影链接和 durable children，并区分不可用记录', async () => {
    const planning = executionSummary('planning-1', 'global.task.plan')
    const attributes = executionSummary('attributes-1', 'category.attribute_fill')
    const wrapper = mount(GlobalAgentChatPanel, {
      props: {
        conversationId: 'conversation-1',
        events: [
          {
            ...taskProjection(),
            name: 'global.agent_execution_link',
            value: { task_id: 'task-1', conversation_id: planning.conversation_id },
          },
          {
            ...taskProjection(),
            seq: 2,
            name: 'global.agent_execution_link',
            value: { task_id: 'task-1', conversation_id: 'missing-1' },
          },
        ],
        executionConversations: [planning, attributes],
        executionConversationsState: 'loaded',
      },
    })

    const rows = wrapper.findAll('[data-testid^="open-agent-execution-"]')
    expect(rows.map((row) => row.attributes('data-testid'))).toEqual([
      'open-agent-execution-planning-1',
      'open-agent-execution-missing-1',
      'open-agent-execution-attributes-1',
    ])
    expect(rows[1].text()).toContain('记录不可用')
    expect(rows[2].text()).toContain('属性补全')
    wrapper.unmount()
  })
})
