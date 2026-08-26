import { beforeEach, describe, expect, it, vi } from 'vitest'
import { apiClient } from '@/api/client'
import {
  approveGlobalTask,
  cancelGlobalTask,
  fetchGlobalTask,
  normalizeExecutionProgress,
  rejectGlobalTask,
  submitGlobalTaskInput,
} from '@/api/globalTasks'

vi.mock('@/api/client', () => ({
  apiClient: {
    get: vi.fn(),
    post: vi.fn(),
  },
}))

const response = {
  ok: true as const,
  task_id: 'gtask-1',
  task: {
    task_id: 'gtask-1',
    goal: '删除商品',
    status: 'pending_approval' as const,
    steps: [],
    current_step_index: 0,
  },
  execution_progress: null,
}

describe('全局任务只读与受信操作 API', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(apiClient.get).mockResolvedValue({
      data: { approvalToken: 'trusted-token' },
    })
    vi.mocked(apiClient.post).mockResolvedValue({ data: response })
  })

  it('读取任务状态走纯 GET，不获取审批凭据也不触发写操作', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: response })

    await expect(fetchGlobalTask('gtask-1')).resolves.toEqual(response)

    expect(apiClient.get).toHaveBeenCalledTimes(1)
    expect(apiClient.get).toHaveBeenCalledWith('/api/v1/global-tasks/gtask-1')
    expect(apiClient.post).not.toHaveBeenCalled()
  })

  it('task_id 中的特殊字符按 URL 编码读取', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: response })

    await fetchGlobalTask('gtask 1/2')

    expect(apiClient.get).toHaveBeenCalledWith('/api/v1/global-tasks/gtask%201%2F2')
  })

  it('提交补充资料走 global-task-input，不携带审批 token', async () => {
    await expect(
      submitGlobalTaskInput('gtask-1', { category_name: '连衣裙' }),
    ).resolves.toEqual(response)

    expect(apiClient.get).not.toHaveBeenCalled()
    expect(apiClient.post).toHaveBeenCalledWith('/api/global-task-input', {
      task_id: 'gtask-1',
      arguments: { category_name: '连衣裙' },
    })
  })

  it('取消任务走 global-task-cancel', async () => {
    await expect(cancelGlobalTask('gtask-1')).resolves.toEqual(response)

    expect(apiClient.post).toHaveBeenCalledWith('/api/global-task-cancel', {
      task_id: 'gtask-1',
    })
  })

  it('批准时即时读取 token 并只放入受信请求头', async () => {
    await expect(approveGlobalTask('gtask-1', 'step-1')).resolves.toEqual(response)

    expect(apiClient.get).toHaveBeenCalledWith('/api/state')
    expect(apiClient.post).toHaveBeenCalledWith(
      '/api/global-task-approve',
      { task_id: 'gtask-1', step_id: 'step-1' },
      { headers: { 'X-Approval-Token': 'trusted-token' } },
    )
  })

  it('拒绝时提交明确原因并携带 token', async () => {
    await rejectGlobalTask('gtask-1', 'step-1', '目标不正确')

    expect(apiClient.post).toHaveBeenCalledWith(
      '/api/global-task-reject',
      { task_id: 'gtask-1', step_id: 'step-1', reason: '目标不正确' },
      { headers: { 'X-Approval-Token': 'trusted-token' } },
    )
  })

  it('缺少 token 时拒绝发送批准请求', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: { approvalToken: '' } })

    await expect(approveGlobalTask('gtask-1', 'step-1')).rejects.toThrow(
      '受信审批凭据缺失',
    )
    expect(apiClient.post).not.toHaveBeenCalled()
  })

  it('GET 返回的进度视图被规范化后随响应一起返回', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: {
        ...response,
        execution_progress: {
          observed_at: '2026-08-24T00:13:00+08:00',
          task_elapsed_seconds: 295,
          current_step: {
            index: 1,
            ordinal: 2,
            total: 4,
            capability_name: 'product_publish_request',
            label: '提交商品发布',
            status: 'running',
          },
          active_job: {
            job_id: 'job-1',
            job_type: 'publish',
            status: 'waiting',
            stage_code: 'confirmation',
            stage_label: '等待平台确认',
            summary: '远端写入已完成',
            started_at: '2026-08-24T00:08:18+08:00',
            updated_at: null,
            elapsed_seconds: 282,
            phase_started_at: null,
            phase_elapsed_seconds: null,
            attempt: 1,
            retry_count: 7,
            next_check_at: '2026-08-24T00:13:02+08:00',
            last_external_status: 'CHECKING',
          },
          activities: [
            { code: 'offer_mapping', label: '提交商品资料', status: 'completed', completed_at: null },
          ],
        },
      },
    })

    const result = await fetchGlobalTask('gtask-1')
    expect(result.execution_progress?.task_elapsed_seconds).toBe(295)
    expect(result.execution_progress?.active_job?.status).toBe('waiting')
    expect(result.execution_progress?.activities).toHaveLength(1)
  })
})

describe('normalizeExecutionProgress 进度字段规范化', () => {
  it('缺失/非法输入返回 null', () => {
    expect(normalizeExecutionProgress(null)).toBeNull()
    expect(normalizeExecutionProgress(undefined)).toBeNull()
    expect(normalizeExecutionProgress('not-an-object')).toBeNull()
    expect(normalizeExecutionProgress({})).toBeNull()
  })

  it('负数与非法耗时收敛为 0', () => {
    const progress = normalizeExecutionProgress({
      observed_at: '2026-08-24T00:13:00+08:00',
      task_elapsed_seconds: -5,
      active_job: { job_id: 'job-1', job_type: 'publish', status: 'running', elapsed_seconds: Number.NaN },
    })
    expect(progress?.task_elapsed_seconds).toBe(0)
    expect(progress?.active_job?.elapsed_seconds).toBe(0)
  })

  it('未知状态回落为 running，空活动列表被过滤', () => {
    const progress = normalizeExecutionProgress({
      observed_at: '2026-08-24T00:13:00+08:00',
      task_elapsed_seconds: 1,
      active_job: { job_id: 'job-1', job_type: 'publish', status: 'some-unknown-status' },
      activities: [{ code: '' }, null, { code: 'price', status: 'weird' }],
    })
    expect(progress?.active_job?.status).toBe('running')
    expect(progress?.activities).toHaveLength(1)
    expect(progress?.activities[0].code).toBe('price')
    expect(progress?.activities[0].status).toBe('running')
  })

  it('缺失 observed_at 时整体视为无进度', () => {
    expect(
      normalizeExecutionProgress({ task_elapsed_seconds: 3 }),
    ).toBeNull()
  })
})
