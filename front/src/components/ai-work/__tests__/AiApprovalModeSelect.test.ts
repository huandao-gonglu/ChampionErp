import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '@/api/aiApprovalMode'
import AiApprovalModeSelect from '../AiApprovalModeSelect.vue'

vi.mock('@/api/aiApprovalMode', () => ({
  fetchAiApprovalMode: vi.fn(),
  saveAiApprovalMode: vi.fn(),
}))

describe('AiApprovalModeSelect 三档审批选择器', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('展示询问审批、自动审批与完全授权三档，不含底部解释文案，并支持切换至自动审批', async () => {
    vi.mocked(api.fetchAiApprovalMode).mockResolvedValue('ask')
    vi.mocked(api.saveAiApprovalMode).mockResolvedValue('auto')

    const wrapper = mount(AiApprovalModeSelect)
    await vi.waitFor(() => {
      expect(wrapper.get('[data-testid="ai-approval-mode-trigger"]').text()).toContain('询问审批')
    })

    await wrapper.get('[data-testid="ai-approval-mode-trigger"]').trigger('click')
    const menu = wrapper.get('[data-testid="ai-approval-mode-menu"]')
    const items = menu.findAll('[role="menuitemradio"]')
    expect(items).toHaveLength(3)
    expect(items.map(item => item.attributes('data-testid'))).toEqual([
      'ai-approval-mode-ask',
      'ai-approval-mode-auto',
      'ai-approval-mode-full',
    ])
    expect(menu.text()).not.toContain('适用于所有对话')
    expect(menu.text()).not.toContain('已批准的操作不因切换而撤销')

    await wrapper.get('[data-testid="ai-approval-mode-auto"]').trigger('click')
    await vi.waitFor(() => {
      expect(api.saveAiApprovalMode).toHaveBeenCalledWith('auto')
      expect(wrapper.get('[data-testid="ai-approval-mode-trigger"]').text()).toContain('自动审批')
      expect(wrapper.find('[data-testid="ai-approval-mode-menu"]').exists()).toBe(false)
    })
    wrapper.unmount()
  })
})
