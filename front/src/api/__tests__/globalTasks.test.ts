import { beforeEach, describe, expect, it, vi } from 'vitest'
import { apiClient } from '@/api/client'
import {
  approveGlobalTask,
  cancelGlobalTask,
  fetchGlobalTask,
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
})
