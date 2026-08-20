import { beforeEach, describe, expect, it, vi } from 'vitest'
import { apiClient } from '@/api/client'
import {
  fetchTaskApprovalMode,
  saveTaskApprovalMode,
} from '@/api/taskApprovalMode'

vi.mock('@/api/client', () => ({
  apiClient: {
    get: vi.fn(),
    post: vi.fn(),
  },
}))

describe('任务审批等级 API', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('从应用状态读取当前等级，未知值安全归为询问审批', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: { appConfig: { task_approval_mode: 'unknown' } },
    })

    await expect(fetchTaskApprovalMode()).resolves.toBe('ask')
    expect(apiClient.get).toHaveBeenCalledWith('/api/state')
  })

  it('保存等级时即时获取 token，并只放入受信请求头', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: { approvalToken: 'trusted-token' },
    })
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: { ok: true, appConfig: { task_approval_mode: 'full' } },
    })

    await expect(saveTaskApprovalMode('full')).resolves.toBe('full')
    expect(apiClient.post).toHaveBeenCalledWith(
      '/api/save-settings',
      { appConfig: { task_approval_mode: 'full' } },
      { headers: { 'X-Approval-Token': 'trusted-token' } },
    )
  })

  it('缺少 token 时不发送保存请求', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: { approvalToken: '' },
    })

    await expect(saveTaskApprovalMode('full')).rejects.toThrow(
      '受信审批凭据缺失',
    )
    expect(apiClient.post).not.toHaveBeenCalled()
  })
})
