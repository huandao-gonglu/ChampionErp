import { beforeEach, describe, expect, it, vi } from 'vitest'
import { apiClient } from '@/api/client'
import {
  approveGlobalTask,
  fetchGlobalTask,
  rejectGlobalTask,
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

describe('全局任务受信审批 API', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(apiClient.get).mockResolvedValue({
      data: { approvalToken: 'trusted-token' },
    })
    vi.mocked(apiClient.post).mockResolvedValue({ data: response })
  })

  it('读取任务状态不获取审批凭据', async () => {
    await expect(fetchGlobalTask('gtask-1')).resolves.toEqual(response)

    expect(apiClient.get).not.toHaveBeenCalled()
    expect(apiClient.post).toHaveBeenCalledWith('/api/global-task-state', {
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
