import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import TaskApprovalModeSelect from '../TaskApprovalModeSelect.vue'
import {
  fetchTaskApprovalMode,
  saveTaskApprovalMode,
} from '@/api/taskApprovalMode'

vi.mock('@/api/taskApprovalMode', () => ({
  fetchTaskApprovalMode: vi.fn(),
  saveTaskApprovalMode: vi.fn(),
}))

describe('任务审批等级选择器', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.restoreAllMocks()
    vi.clearAllMocks()
    vi.mocked(fetchTaskApprovalMode).mockResolvedValue('ask')
    vi.mocked(saveTaskApprovalMode).mockResolvedValue('full')
    vi.spyOn(window, 'confirm').mockReturnValue(true)
  })

  it('显示两个等级，并在确认后保存完全授权', async () => {
    const wrapper = mount(TaskApprovalModeSelect)
    await flushPromises()

    expect(wrapper.get('[data-testid="task-approval-mode-trigger"]').text())
      .toContain('询问审批')

    await wrapper.get('[data-testid="task-approval-mode-trigger"]').trigger('click')
    expect(wrapper.get('[data-testid="task-approval-mode-ask"]').text())
      .toContain('询问审批')
    expect(wrapper.get('[data-testid="task-approval-mode-full"]').text())
      .toContain('完全授权')

    await wrapper.get('[data-testid="task-approval-mode-full"]').trigger('click')
    await flushPromises()

    expect(window.confirm).toHaveBeenCalledOnce()
    expect(saveTaskApprovalMode).toHaveBeenCalledWith('full')
    expect(wrapper.get('[data-testid="task-approval-mode-trigger"]').text())
      .toContain('完全授权')
  })

  it('取消警告时不保存完全授权', async () => {
    vi.mocked(window.confirm).mockReturnValue(false)
    const wrapper = mount(TaskApprovalModeSelect)
    await flushPromises()

    await wrapper.get('[data-testid="task-approval-mode-trigger"]').trigger('click')
    await wrapper.get('[data-testid="task-approval-mode-full"]').trigger('click')

    expect(saveTaskApprovalMode).not.toHaveBeenCalled()
    expect(wrapper.get('[data-testid="task-approval-mode-trigger"]').text())
      .toContain('询问审批')
  })
})
