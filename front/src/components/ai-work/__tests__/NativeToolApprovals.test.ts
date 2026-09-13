import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { Chat } from '@ai-sdk/vue'
import type { UIMessage } from 'ai'
import NativeToolApprovals from '../NativeToolApprovals.vue'
import { useAiChatStore } from '@/stores/aiChat'

describe('原生工具审批卡', () => {
  beforeEach(() => setActivePinia(createPinia()))
  it('只展示服务端当前未决定的审批，使用原生 approval ID 提交', async () => {
    const store = useAiChatStore()
    store.chat = new Chat<UIMessage>({ id: 'approval-test', messages: [{ id: 'assistant', role: 'assistant', parts: [
      { type: 'tool-publish', toolCallId: 'call-1', state: 'approval-requested', input: { draft_id: 'draft-a' }, approval: { id: 'call-1' } },
      { type: 'tool-prepare', toolCallId: 'call-2', state: 'input-available', input: { draft_id: 'draft-b' } },
    ] }] })
    store.pendingToolCalls = [{ tool_call_id: 'call-1', tool_name: 'publish', kind: 'approval', summary: '发布指定草稿' }, { tool_call_id: 'call-2', tool_name: 'prepare', kind: 'external', summary: '' }]
    const respond = vi.spyOn(store, 'respondToApproval').mockResolvedValue()
    const wrapper = mount(NativeToolApprovals)
    expect(wrapper.findAll('section')).toHaveLength(1)
    expect(wrapper.text()).toContain('发布指定草稿')
    await wrapper.findAll('button')[1].trigger('click')
    await flushPromises()
    expect(respond).toHaveBeenCalledWith('call-1', false, '用户拒绝此操作')
    store.pendingToolCalls = store.pendingToolCalls.filter(call => call.kind === 'external')
    await flushPromises()
    expect(wrapper.find('[data-testid="native-tool-approvals"]').exists()).toBe(false)
  })
})
