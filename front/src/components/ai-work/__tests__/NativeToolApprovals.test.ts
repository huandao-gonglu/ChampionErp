import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { Chat } from '@ai-sdk/vue'
import type { UIMessage } from 'ai'
import NativeToolApprovals from '../NativeToolApprovals.vue'
import ChatPendingActions from '../ChatPendingActions.vue'
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
  it('待确认项留在输入框上方，展开后使用对应原生 ID', async () => {
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
      const scroll = wrapper.get('[data-testid="ai-chat-scroll"]')
      expect(scroll.find('[data-testid="native-tool-approvals"]').exists()).toBe(false)
      const queue = wrapper.get('[aria-label="等待你处理"]')
      expect(scroll.element.contains(queue.element)).toBe(false)
      expect(queue.find('[data-testid="native-tool-approvals"]').exists()).toBe(false)
      const rows = queue.findAll('button[aria-expanded]')
      expect(rows.map(row => row.text())).toEqual(['确认 send-1', '确认 send-2'])
      await rows[1].trigger('click')
      const card = queue.get('[data-testid="native-tool-approvals"]')
      expect(card.get('pre').text()).toContain('第二条完整消息')
      await card.findAll('button')[0].trigger('click')
      expect(respond).toHaveBeenCalledTimes(1)
      expect(respond).toHaveBeenCalledWith('approval-2', true, undefined)
      expect(wrapper.findAll('button.btn').every(button => button.attributes('disabled') !== undefined)).toBe(true)
      rejectSubmission(new Error('提交失败，请重试'))
      await flushPromises()
      expect(card.get('[role="alert"]').text()).toBe('提交失败，请重试')
      expect(rows[0].find('[role="alert"]').exists()).toBe(false)
      expect(wrapper.findAll('button.btn').every(button => button.attributes('disabled') === undefined)).toBe(true)
    } finally {
      wrapper.unmount()
    }
  })

  it('主任务和即时回复的同名调用分别保留草稿，并按提问时间排列', async () => {
    const store = useAiChatStore()
    store.activeConversationId = 'main'
    const question = { tool_call_id: 'same', tool_name: 'request_user_input', kind: 'input' as const,
      summary: '', title: '确认颜色', question: '请选择商品的颜色', options: ['蓝色'] }
    store.pendingToolCalls = [{ ...question, requested_at: '2026-11-08T10:02:00Z' }]
    store.parallelReplies = [{ ok: true, conversation_id: 'parallel', history_version: 1, messages: [], created_at: '', updated_at: '',
      pending_tool_calls: [{ ...question, requested_at: '2026-11-08T10:01:00Z', title: '确认数量', question: '采购多少件？' }] }]
    const answer = vi.spyOn(store, 'answerQuestion').mockRejectedValue(new Error('暂时无法连接'))
    const wrapper = mount(ChatPendingActions)
    const [first, second] = wrapper.findAll('button[aria-expanded]')
    expect([first.text(), second.text()]).toEqual(['确认数量', '确认颜色'])
    expect(wrapper.find('textarea').exists()).toBe(false)
    await first.trigger('click')
    await wrapper.get('textarea').setValue('12')
    await second.trigger('click')
    expect(wrapper.get('textarea').element.value).toBe('')
    await wrapper.get('button[aria-pressed]').trigger('click')
    expect(wrapper.get('textarea').element.value).toBe('蓝色')
    await first.trigger('click')
    expect(wrapper.get('textarea').element.value).toBe('12')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(answer).toHaveBeenCalledWith('parallel', 'same', '12')
    expect(wrapper.get('[role="alert"]').text()).toBe('暂时无法连接')
    expect(wrapper.get('textarea').element.value).toBe('12')
    store.parallelReplies = []
    await flushPromises()
    expect(wrapper.find('textarea').exists()).toBe(false)
    await second.trigger('click')
    expect(wrapper.get('textarea').element.value).toBe('蓝色')
    wrapper.unmount()
  })

})
