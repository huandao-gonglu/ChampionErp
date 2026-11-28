import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { Chat } from '@ai-sdk/vue'
import type { UIMessage } from 'ai'
import ChatPendingActions from '../ChatPendingActions.vue'
import AiChatPanel from '../AiChatPanel.vue'
import { useAiChatStore } from '@/stores/aiChat'

describe('原生工具审批与步进问答卡', () => {
  beforeEach(() => setActivePinia(createPinia()))
  afterEach(() => vi.unstubAllGlobals())

  it('在确认前展示原规则与将保存的正文', async () => {
    const store = useAiChatStore()
    const input = { current_text: '先给结果。', text: '先给结果，再列出失败的 SKU。' }
    store.chat = new Chat<UIMessage>({ id: 'rule-review', messages: [{ id: 'a', role: 'assistant', parts: [{
      type: 'tool-user_agents_update', toolCallId: 'rule', input, state: 'approval-requested', approval: { id: 'rule' },
    }] }] })
    store.pendingToolCalls = [{ tool_call_id: 'rule', tool_name: 'user_agents_update', kind: 'approval', summary: '确认替换自定义指令' }]
    const respond = vi.spyOn(store, 'respondToApproval').mockResolvedValue()
    const wrapper = mount(ChatPendingActions)
    try {
      expect(wrapper.get('[data-testid="chat-pending-toggle"]').attributes('aria-expanded')).toBe('true')
      expect(wrapper.get('[data-testid="chat-pending-counter"]').text()).toBe('1 / 1')
      const card = wrapper.get('[data-testid="native-tool-approvals"]')
      expect(card.findAll('dd').map(item => item.text())).toEqual([input.current_text, input.text])
      expect(card.find('details').exists()).toBe(false)
      await card.get('button.btn-primary').trigger('click')
      expect(respond).toHaveBeenCalledWith('rule', true, undefined)
    } finally {
      wrapper.unmount()
    }
  })

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
    const wrapper = mount(ChatPendingActions)
    try {
      expect(wrapper.get('[data-testid="chat-pending-toggle"]').attributes('aria-expanded')).toBe('true')
      const review = wrapper.get('[aria-label="待确认的包装外尺寸"]').text()
      for (const text of ['10 L 桶', 's-1', '包装长 30 cm', '28–32 cm', input.estimates[0].basis]) expect(review).toContain(text)
      expect(respond).not.toHaveBeenCalled()
      await wrapper.get('button.btn-primary').trigger('click')
      expect(respond).toHaveBeenCalledWith('estimate', true, undefined)
    } finally {
      wrapper.unmount()
    }
  })

  it('只展示服务端当前未决定的审批，使用原生 approval ID 提交', async () => {
    const store = useAiChatStore()
    store.chat = new Chat<UIMessage>({ id: 'approval-test', messages: [{ id: 'assistant', role: 'assistant', parts: [
      { type: 'tool-publish', toolCallId: 'call-1', state: 'approval-requested', input: { draft_id: 'draft-a' }, approval: { id: 'call-1' } },
      { type: 'tool-prepare', toolCallId: 'call-2', state: 'input-available', input: { draft_id: 'draft-b' } },
    ] }] })
    store.pendingToolCalls = [{ tool_call_id: 'call-1', tool_name: 'publish', kind: 'approval', summary: '发布指定草稿' }, { tool_call_id: 'call-2', tool_name: 'prepare', kind: 'external', summary: '' }]
    const respond = vi.spyOn(store, 'respondToApproval').mockResolvedValue()
    const wrapper = mount(ChatPendingActions)
    try {
      expect(wrapper.get('[data-testid="chat-pending-counter"]').text()).toBe('1 / 1')
      expect(wrapper.text()).toContain('发布指定草稿')
      await wrapper.get('button.btn-outline').trigger('click')
      await flushPromises()
      expect(respond).toHaveBeenCalledWith('call-1', false, '用户拒绝此操作')
      store.pendingToolCalls = store.pendingToolCalls.filter(call => call.kind === 'external')
      await flushPromises()
      expect(wrapper.find('[data-testid="chat-pending-actions"]').exists()).toBe(false)
    } finally {
      wrapper.unmount()
    }
  })

  it('多项待确认通过步进头切换，提交时使用对应原生 ID 并在失败后提示重审', async () => {
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
      expect(queue.get('[data-testid="chat-pending-counter"]').text()).toBe('1 / 2')
      expect(queue.get('[data-testid="chat-pending-header"]').text()).toContain('确认 send-1')

      await queue.get('[data-testid="chat-pending-next"]').trigger('click')
      expect(queue.get('[data-testid="chat-pending-counter"]').text()).toBe('2 / 2')
      const card = queue.get('[data-testid="native-tool-approvals"]')
      expect(card.get('pre').text()).toContain('第二条完整消息')

      await card.get('button.btn-primary').trigger('click')
      expect(respond).toHaveBeenCalledTimes(1)
      expect(respond).toHaveBeenCalledWith('approval-2', true, undefined)
      // 提交第 2 项后自动切到待处理的第 1 项，不阻塞用户处理其余待办。
      expect(queue.get('[data-testid="chat-pending-counter"]').text()).toBe('1 / 2')

      rejectSubmission(new Error('提交失败，请重试'))
      await flushPromises()
      expect(queue.get('[role="alert"]').text()).toContain('提交失败，请重试')

      await queue.get('[data-testid="chat-pending-next"]').trigger('click')
      expect(queue.get('[role="alert"]').text()).toBe('提交失败，请重试')
      expect(queue.get('[data-testid="native-tool-approvals"] button.btn-primary').attributes('disabled')).toBeUndefined()
    } finally {
      wrapper.unmount()
    }
  })

  it('主任务和即时回复的同名调用按提问时间排列，选项高亮、自定义回复与草稿互不干扰', async () => {
    const store = useAiChatStore()
    store.activeConversationId = 'main'
    const question = { tool_call_id: 'same', tool_name: 'request_user_input', kind: 'input' as const,
      summary: '', title: '确认颜色', question: '请选择商品的颜色', options: ['蓝色', '黑色'] }
    store.pendingToolCalls = [{ ...question, requested_at: '2026-11-08T10:02:00Z' }]
    store.parallelReplies = [{ ok: true, conversation_id: 'parallel', history_version: 1, messages: [], created_at: '', updated_at: '',
      pending_tool_calls: [{ ...question, options: undefined, requested_at: '2026-11-08T10:01:00Z', title: '确认数量', question: '采购多少件？' }] }]
    let rejectAnswer!: (error: Error) => void
    const answer = vi.spyOn(store, 'answerQuestion').mockImplementation(() => new Promise((_, reject) => { rejectAnswer = reject }))
    const wrapper = mount(ChatPendingActions)
    try {
      // 按时间排序：第 1 项为无选项的「确认数量」，直接显示输入框。
      expect(wrapper.get('[data-testid="chat-pending-counter"]').text()).toBe('1 / 2')
      expect(wrapper.get('[data-testid="chat-pending-header"]').text()).toContain('确认数量')
      await wrapper.get('textarea').setValue('12')

      // 切到第 2 项（有选项的「确认颜色」）：默认展示纵向选项行与「其他回复」入口，不常驻 textarea。
      await wrapper.get('[data-testid="chat-pending-next"]').trigger('click')
      expect(wrapper.get('[data-testid="chat-pending-header"]').text()).toContain('确认颜色')
      expect(wrapper.find('textarea').exists()).toBe(false)
      const options = wrapper.findAll('[data-testid="chat-pending-option"]')
      expect(options).toHaveLength(2)
      await options[0]!.trigger('click')
      expect(options[0]!.attributes('aria-pressed')).toBe('true')
      expect(options[1]!.attributes('aria-pressed')).toBe('false')

      // 展开「其他回复（手动输入）」再取消，再重新选中「蓝色」。
      await wrapper.get('[data-testid="chat-pending-custom-toggle"]').trigger('click')
      expect(wrapper.find('textarea').exists()).toBe(true)
      await wrapper.get('[data-testid="chat-pending-custom-cancel"]').trigger('click')
      expect(wrapper.find('textarea').exists()).toBe(false)
      await wrapper.findAll('[data-testid="chat-pending-option"]')[0]!.trigger('click')

      // 切回第 1 项，原有草稿「12」完整保留。
      await wrapper.get('[data-testid="chat-pending-prev"]').trigger('click')
      expect(wrapper.get('textarea').element.value).toBe('12')

      await wrapper.get('[data-testid="chat-pending-submit"]').trigger('click')
      expect(answer).toHaveBeenCalledWith('parallel', 'same', '12')
      // 自动切往第 2 项继续查看。
      expect(wrapper.get('[data-testid="chat-pending-counter"]').text()).toBe('2 / 2')

      rejectAnswer(new Error('暂时无法连接'))
      await flushPromises()
      expect(wrapper.get('[role="alert"]').text()).toContain('暂时无法连接')
      await wrapper.get('[data-testid="chat-pending-prev"]').trigger('click')
      expect(wrapper.get('textarea').element.value).toBe('12')

      store.parallelReplies = []
      await flushPromises()
      // 仅剩第 2 项「确认颜色」，先前选中的「蓝色」状态完整保留。
      expect(wrapper.get('[data-testid="chat-pending-counter"]').text()).toBe('1 / 1')
      expect(wrapper.findAll('[data-testid="chat-pending-option"]')[0]!.attributes('aria-pressed')).toBe('true')
    } finally {
      wrapper.unmount()
    }
  })

  it('单条 AI 问答默认展开完整问题与回复框，用户手动折叠后在同条刷新时保持收起', async () => {
    const store = useAiChatStore()
    store.activeConversationId = 'main'
    store.pendingToolCalls = [{
      tool_call_id: 'ask-1',
      tool_name: 'request_user_input',
      kind: 'input',
      summary: '',
      title: '确认包装尺寸与库存',
      question: '请补充各规格的外包装尺寸和可售库存。',
    }]
    const wrapper = mount(ChatPendingActions)
    try {
      expect(wrapper.get('[data-testid="chat-pending-header"]').text()).toContain('确认包装尺寸与库存')
      expect(wrapper.get('[data-testid="chat-pending-header"]').text()).toContain('补充信息')
      expect(wrapper.get('[data-testid="chat-pending-toggle"]').attributes('aria-expanded')).toBe('true')
      expect(wrapper.text()).toContain('请补充各规格的外包装尺寸和可售库存。')
      expect(wrapper.find('textarea').exists()).toBe(true)

      await wrapper.get('[data-testid="chat-pending-toggle"]').trigger('click')
      expect(wrapper.get('[data-testid="chat-pending-toggle"]').attributes('aria-expanded')).toBe('false')
      expect(wrapper.find('textarea').exists()).toBe(false)

      // 刷新同条待办后尊重用户手动折叠状态。
      store.pendingToolCalls = [{ ...store.pendingToolCalls[0]! }]
      await flushPromises()
      expect(wrapper.find('textarea').exists()).toBe(false)
    } finally {
      wrapper.unmount()
    }
  })

  it('审批内容过长时，待处理区直接受对话面板高度约束且内部可滚动，批准操作栏保持吸底可达', () => {
    vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
    const store = useAiChatStore()
    const estimates = Array.from({ length: 12 }, (_, index) => ({
      sku_id: `sku-${index}`,
      sku_name: `规格 ${index + 1}`,
      basis: '按商品尺寸与纸箱预留空间估算。',
      dimensions: {
        length_cm: { low: 48, suggested: 52, high: 55 },
        width_cm: { low: 6, suggested: 7, high: 9 },
        height_cm: { low: 4, suggested: 5, high: 7 },
      },
    }))
    store.chat = new Chat<UIMessage>({
      id: 'long-approval-scroll',
      messages: [{
        id: 'assistant',
        role: 'assistant',
        parts: [{
          type: 'tool-draft_package_estimates_confirm',
          toolCallId: 'long-estimate',
          state: 'approval-requested',
          input: { estimates },
          approval: { id: 'long-estimate' },
        }],
      }],
    })
    store.pendingToolCalls = [{
      tool_call_id: 'long-estimate',
      tool_name: 'draft_package_estimates_confirm',
      kind: 'approval',
      summary: '确认 12 个规格的包装尺寸估算',
    }]

    const wrapper = mount(AiChatPanel, {
      props: { layout: 'page', messages: store.messages, busy: false, input: '' },
      global: { stubs: { AiChatComposer: true } },
    })
    try {
      const panel = wrapper.get('[data-testid="ai-chat-panel"]')
      const queue = wrapper.get('[data-testid="chat-pending-actions"]')
      const queueScroll = wrapper.get('[data-testid="chat-pending-scroll"]')
      const actions = wrapper.get('[data-testid="native-tool-approval-actions"]')

      // ChatPendingActions 必须作为定高 flex-col 面板的直接子元素，max-h-[45%] 才能生效。
      expect(panel.classes()).toContain('overflow-hidden')
      expect(queue.element.parentElement).toBe(panel.element)
      expect(queue.classes()).toEqual(expect.arrayContaining(['flex', 'max-h-[45%]', 'min-h-0', 'flex-col', 'overflow-hidden', 'mx-6']))
      expect(queueScroll.classes()).toEqual(expect.arrayContaining(['min-h-0', 'flex-1', 'overflow-y-auto']))
      expect(actions.classes()).toEqual(expect.arrayContaining(['sticky', 'bottom-0']))
      expect(actions.findAll('button').map(btn => btn.text())).toEqual(['拒绝', '批准'])
    } finally {
      wrapper.unmount()
    }
  })
})
