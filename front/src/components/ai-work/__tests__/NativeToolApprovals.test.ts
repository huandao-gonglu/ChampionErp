import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { Chat } from '@ai-sdk/vue'
import type { UIMessage } from 'ai'
import NativeToolApprovals from '../NativeToolApprovals.vue'
import AiChatPanel from '../AiChatPanel.vue'
import { useAiChatStore } from '@/stores/aiChat'

describe('原生工具审批卡', () => {
  beforeEach(() => setActivePinia(createPinia()))
  afterEach(() => vi.unstubAllGlobals())
  it('核对每个 SKU 的建议值、区间和依据后，才提交原生确认', async () => {
    const store = useAiChatStore()
    const input = { estimates: [{ sku_id: 's-1', sku_name: '10 L 桶', basis: '按桶身标注预留纸箱空间', dimensions: {
      length_cm: { low: 28, suggested: 30, high: 32 },
    } }] }
    store.chat = new Chat<UIMessage>({ id: 'size-review', messages: [{ id: 'a', role: 'assistant', parts: [{
      type: 'tool-draft_package_estimates_confirm', toolCallId: 'estimate', input, state: 'approval-requested', approval: { id: 'estimate' },
    }] }] })
    store.pendingToolCalls = [{ tool_call_id: 'estimate', tool_name: 'draft_package_estimates_confirm', kind: 'approval', summary: '确认建议值' }]
    const respond = vi.spyOn(store, 'respondToApproval').mockResolvedValue()
    const wrapper = mount(NativeToolApprovals)
    const review = wrapper.get('[aria-label="待确认的包装外尺寸"]').text()
    for (const text of ['10 L 桶', 's-1', '包装长 30 cm', '28–32 cm', input.estimates[0].basis]) expect(review).toContain(text)
    expect(respond).not.toHaveBeenCalled()
    await wrapper.get('button').trigger('click')
    expect(respond).toHaveBeenCalledWith('estimate', true, undefined)
  })
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
  it('确认按钮就地显示且不重复，多个审批共享提交状态并使用对应原生 ID', async () => {
    vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
    const store = useAiChatStore()
    store.chat = new Chat<UIMessage>({ id: 'inline-approval-test', messages: [{ id: 'assistant', role: 'assistant', parts: [
      { type: 'text', text: '请确认这两条消息。' },
      { type: 'tool-order_buyer_chat_send', toolCallId: 'send-1', state: 'approval-requested', input: { text: '第一条完整消息' }, approval: { id: 'approval-1' } },
      { type: 'tool-order_buyer_chat_send', toolCallId: 'send-2', state: 'approval-requested', input: { text: '第二条完整消息' }, approval: { id: 'approval-2' } },
    ] }] })
    store.pendingToolCalls = ['send-1', 'send-2'].map(id => ({ tool_call_id: id, tool_name: 'order_buyer_chat_send', kind: 'approval', summary: `确认 ${id}` }))
    let rejectSubmission!: (error: Error) => void
    const respond = vi.spyOn(store, 'respondToApproval').mockImplementation(() => new Promise((_, reject) => { rejectSubmission = reject }))
    const wrapper = mount(AiChatPanel, {
      props: { messages: store.messages, busy: false, input: '' },
      global: { stubs: { AiChatComposer: true } },
    })
    try {
      const rows = wrapper.findAll('[data-testid="ai-part-tool"]')
      expect(rows).toHaveLength(2)
      expect(wrapper.findAll('[data-testid="native-tool-approvals"]')).toHaveLength(2)
      for (const [index, row] of rows.entries()) {
        const approval = row.get('[data-testid="native-tool-approvals"]')
        expect(approval.text()).toContain(`确认 send-${index + 1}`)
        expect(approval.element.closest('details')).toBeNull()
        expect(approval.get('summary').text()).toBe('查看操作参数')
      }
      expect(rows[1].get('pre').text()).toContain('第二条完整消息')
      await rows[1].findAll('button')[0].trigger('click')
      expect(respond).toHaveBeenCalledTimes(1)
      expect(respond).toHaveBeenCalledWith('approval-2', true, undefined)
      expect(wrapper.findAll('button.btn').every(button => button.attributes('disabled') !== undefined)).toBe(true)
      rejectSubmission(new Error('提交失败，请重试'))
      await flushPromises()
      expect(rows[1].get('[role="alert"]').text()).toBe('提交失败，请重试')
      expect(rows[0].find('[role="alert"]').exists()).toBe(false)
      expect(wrapper.findAll('button.btn').every(button => button.attributes('disabled') === undefined)).toBe(true)
    } finally {
      wrapper.unmount()
    }
  })

})
