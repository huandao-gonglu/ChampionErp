import { beforeEach, describe, expect, it, vi } from 'vitest'
import { apiClient } from '@/api/client'
import {
  cancelGlobalTask,
  confirmGlobalTaskPublish,
  fetchGlobalTaskState,
  startGlobalTask,
  submitGlobalTaskInput,
} from '@/api/globalTasks'

vi.mock('@/api/client', () => ({
  apiClient: {
    post: vi.fn(),
  },
}))

const response = {
  ok: true as const,
  task_id: 'task-1',
  ai_work_conversation_id: 'conversation-1',
  task: {
    schema_version: 1 as const,
    task_id: 'task-1',
    task_kind: 'global.agent.chat',
    goal: '准备草稿',
    product_id: '',
    platform: '',
    status: 'running' as const,
    steps: [],
    current_step_index: 0,
    pending_inputs: [],
    pending_input_owner: 'none' as const,
    publish_confirmation: {
      status: 'none' as const,
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
  },
}

describe('global task API contract', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(apiClient.post).mockResolvedValue({ data: response })
  })

  it('首条目标与同会话后续目标使用同一个 start 端点', async () => {
    await startGlobalTask({
      goal: '发布第二个草稿',
      task_kind: 'global.agent.chat',
      ai_work_conversation_id: 'conversation-1',
      draft_query_snapshot_id: 'snapshot-1',
    })

    expect(apiClient.post).toHaveBeenCalledWith('/api/global-task-start', {
      goal: '发布第二个草稿',
      task_kind: 'global.agent.chat',
      ai_work_conversation_id: 'conversation-1',
      draft_query_snapshot_id: 'snapshot-1',
    })
  })

  it('状态、缺失资料、确认发布和取消均使用显式端点', async () => {
    await fetchGlobalTaskState('task-1')
    await submitGlobalTaskInput({
      task_id: 'task-1',
      message: '电池类型是锂电池',
      inputs: { battery_type: '锂电池' },
    })
    await confirmGlobalTaskPublish('task-1')
    await cancelGlobalTask('task-1')

    expect(vi.mocked(apiClient.post).mock.calls).toEqual([
      ['/api/global-task-state', { task_id: 'task-1' }],
      ['/api/global-task-input', {
        task_id: 'task-1',
        message: '电池类型是锂电池',
        inputs: { battery_type: '锂电池' },
      }],
      ['/api/global-task-publish-confirm', { task_id: 'task-1' }],
      ['/api/global-task-cancel', { task_id: 'task-1' }],
    ])
  })
})
