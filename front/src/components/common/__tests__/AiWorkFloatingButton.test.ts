import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { defineComponent } from 'vue'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { Chat } from '@ai-sdk/vue'
import type { UIMessage } from 'ai'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useAiChatStore, useAiWorkDisplayStore } from '@/stores'
import AiChatPanel from '@/components/ai-work/AiChatPanel.vue'
import AiWorkFloatingButton from '../AiWorkFloatingButton.vue'

const mocks = vi.hoisted(() => ({
  fetchUiMessages: vi.fn(),
  fetchConversationTaskLink: vi.fn(),
  fetchGlobalTask: vi.fn(),
  approveGlobalTask: vi.fn(),
  rejectGlobalTask: vi.fn(),
  submitGlobalTaskInput: vi.fn(),
  cancelGlobalTask: vi.fn(),
}))

vi.mock('@/api/aiWork', () => ({
  AI_CHAT_RUNS_PATH: '/api/v1/ai-chat/runs',
  conversationEventsUrl: (conversationId: string, afterHistoryVersion: number) => (
    `/api/v1/ai-work/conversations/${conversationId}/events`
    + `?after_history_version=${Math.max(0, Math.floor(afterHistoryVersion))}`
  ),
  fetchConversationTaskLink: mocks.fetchConversationTaskLink,
  fetchUiMessages: mocks.fetchUiMessages,
}))

vi.mock('@/api/globalTasks', () => ({
  fetchGlobalTask: mocks.fetchGlobalTask,
  approveGlobalTask: mocks.approveGlobalTask,
  rejectGlobalTask: mocks.rejectGlobalTask,
  submitGlobalTaskInput: mocks.submitGlobalTaskInput,
  cancelGlobalTask: mocks.cancelGlobalTask,
}))

const NoopView = defineComponent({ render: () => null })

function createTestRouter(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'WorkflowHome', component: NoopView },
      { path: '/aiWork', name: 'AiWork', component: NoopView },
    ],
  })
}

async function mountFloatingButton() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const router = createTestRouter()
  await router.push('/')
  await router.isReady()
  const wrapper = mount(AiWorkFloatingButton, {
    global: { plugins: [pinia, router] },
  })
  return { wrapper, store: useAiChatStore(), display: useAiWorkDisplayStore(), router }
}

function fakeObserveChat(messages: UIMessage[] = []): Chat<UIMessage> {
  return new Chat<UIMessage>({ id: 'presentation_x', messages })
}

function attachPresentation(
  display: ReturnType<typeof useAiWorkDisplayStore>,
  messages: UIMessage[] = [],
): void {
  display.attachForegroundPresentation(
    {
      presentationId: 'presentation_x',
      conversationId: 'conversation_presentation_x',
      displayTitle: 'AI 匹配类目',
      status: 'running',
    },
    fakeObserveChat(messages),
  )
}

describe('AiWorkFloatingButton', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.fetchConversationTaskLink.mockImplementation(async (conversationId: string) => ({
      ok: true,
      conversation_id: conversationId,
      task_id: '',
      link_status: '',
      task: null,
    }))
    mocks.fetchGlobalTask.mockImplementation(async (taskId: string) => ({
      ok: true,
      task_id: taskId,
      task: {
        task_id: taskId,
        goal: '后台任务',
        status: 'in_progress',
        steps: [],
        current_step_index: 0,
      },
    }))
  })

  it('默认渲染 SPA 链接，指向 /aiWork 且不携带 target/rel', async () => {
    const { wrapper } = await mountFloatingButton()

    const link = wrapper.get('[data-testid="ai-work-floating-toggle"]')
    expect(link.element.tagName).toBe('A')
    expect(link.attributes('href')).toBe('/aiWork')
    expect(link.attributes('target')).toBeUndefined()
    expect(link.attributes('rel')).toBeUndefined()
    expect(link.attributes('aria-label')).toBe('打开 AI Work')
    expect(wrapper.find('[target="_blank"]').exists()).toBe(false)
    expect(wrapper.find('[role="region"]').exists()).toBe(false)
    expect(link.find('path').attributes('d')).toBe(
      'M7.5 17.5 4 20v-4.3A7.5 7.5 0 0 1 2.5 11C2.5 6.9 6.5 3.5 11.5 3.5S20.5 6.9 20.5 11s-4 7.5-9 7.5c-1.45 0-2.8-.28-4-.78Z',
    )
  })

  it('鼠标悬停时自动显示活动对话，移出后自动收起', async () => {
    const { wrapper, store } = await mountFloatingButton()
    const floating = wrapper.get('[data-testid="ai-work-floating"]')

    await floating.trigger('mouseenter')

    expect(store.floatingOpen).toBe(true)
    const panel = wrapper.get('[role="region"]')
    expect(panel.attributes('aria-label')).toBe('全局 AI 浮动对话')
    expect(panel.find('[data-testid="ai-chat-panel"]').exists()).toBe(true)
    expect(panel.find('[data-testid="ai-chat-input"]').exists()).toBe(true)

    await floating.trigger('mouseleave')
    expect(store.floatingOpen).toBe(false)
    expect(wrapper.find('[role="region"]').exists()).toBe(false)
  })

  it('输入与发送都经过共享 store，不产生本地第二份状态', async () => {
    const { wrapper, store } = await mountFloatingButton()
    const sendSpy = vi.spyOn(store, 'sendMessage').mockImplementation(() => {})

    await wrapper.get('[data-testid="ai-work-floating"]').trigger('mouseenter')
    await wrapper.get('[data-testid="ai-chat-input"]').setValue('帮我看看草稿')
    expect(store.input).toBe('帮我看看草稿')

    await wrapper.get('[data-testid="ai-chat-composer"]').trigger('submit')
    expect(sendSpy).toHaveBeenCalledTimes(1)
  })

  it('浮层面板把 conversation id 传给 AiChatPanel 并在未解决任务存在时挂载任务卡', async () => {
    const { wrapper, store } = await mountFloatingButton()
    const conversationId = store.startConversation()
    mocks.fetchConversationTaskLink.mockResolvedValue({
      ok: true,
      conversation_id: conversationId,
      task_id: 'gtask-7',
      link_status: 'ready',
      task: null,
    })

    await wrapper.get('[data-testid="ai-work-floating"]').trigger('mouseenter')

    const panel = wrapper.findComponent(AiChatPanel)
    expect(panel.exists()).toBe(true)
    expect(panel.props('conversationId')).toBe(conversationId)

    await vi.waitFor(() => {
      expect(wrapper.find('[data-testid="global-task-card"]').exists()).toBe(true)
    })
    expect(mocks.fetchGlobalTask).toHaveBeenCalledWith('gtask-7')
    expect(wrapper.find('[data-testid="ai-chat-send-blocked"]').exists()).toBe(true)

    wrapper.unmount()
  })

  it('关闭按钮收起面板', async () => {
    const { wrapper, store } = await mountFloatingButton()

    await wrapper.get('[data-testid="ai-work-floating"]').trigger('mouseenter')
    expect(wrapper.find('[role="region"]').exists()).toBe(true)

    await wrapper.get('[data-testid="ai-work-floating-close"]').trigger('click')
    expect(store.floatingOpen).toBe(false)
    expect(wrapper.find('[role="region"]').exists()).toBe(false)
  })

  it('活动气泡链接携带 conversation query，且不再是新标签页链接', async () => {
    const { wrapper, store } = await mountFloatingButton()
    const conversationId = store.startConversation()

    await wrapper.vm.$nextTick()
    const link = wrapper.get('[data-testid="ai-work-floating-toggle"]')

    expect(link.attributes('href')).toBe(`/aiWork?conversation_id=${conversationId}`)
    expect(link.attributes('target')).toBeUndefined()
    expect(link.attributes('rel')).toBeUndefined()
  })

  it('普通点击在当前标签页 SPA 导航，不清空 store 也不停止流', async () => {
    const { wrapper, store, router } = await mountFloatingButton()
    const conversationId = store.startConversation()
    store.input = '帮我看看草稿'
    const stopSpy = vi.spyOn(store, 'stopStreaming')
    const newConversationSpy = vi.spyOn(store, 'newConversation')

    await wrapper.vm.$nextTick()
    await wrapper.get('[data-testid="ai-work-floating"]').trigger('mouseenter')
    expect(store.floatingOpen).toBe(true)

    await wrapper.get('[data-testid="ai-work-floating-toggle"]').trigger('click')
    await flushPromises()

    expect(router.currentRoute.value.name).toBe('AiWork')
    expect(router.currentRoute.value.path).toBe('/aiWork')
    expect(router.currentRoute.value.query.conversation_id).toBe(conversationId)

    // 导航只收起面板并隐藏浮动入口，不能重建/清空活动 Chat。
    expect(store.floatingOpen).toBe(false)
    expect(wrapper.find('[data-testid="ai-work-floating"]').exists()).toBe(false)
    expect(store.input).toBe('帮我看看草稿')
    expect(store.activeConversationId).toBe(conversationId)
    expect(store.chat).not.toBeNull()
    expect(store.chat?.id).toBe(conversationId)
    expect(stopSpy).not.toHaveBeenCalled()
    expect(newConversationSpy).not.toHaveBeenCalled()
  })

  it('没有活动 conversation 时点击进入不带 query 的 /aiWork', async () => {
    const { wrapper, router } = await mountFloatingButton()

    await wrapper.get('[data-testid="ai-work-floating-toggle"]').trigger('click')
    await flushPromises()

    expect(router.currentRoute.value.path).toBe('/aiWork')
    expect(router.currentRoute.value.query.conversation_id).toBeUndefined()
  })

  it('悬浮聊天内容中不显示 AiWork 入口按钮', async () => {
    const { wrapper } = await mountFloatingButton()

    await wrapper.get('[data-testid="ai-work-floating"]').trigger('mouseenter')

    const panel = wrapper.get('[data-testid="ai-work-floating-panel"]')
    expect(panel.find('[data-testid="ai-work-floating-open-full"]').exists()).toBe(false)
    expect(panel.text()).not.toContain('打开完整对话')
  })

  it('在 AI Work 页面隐藏入口', async () => {
    const { wrapper, router } = await mountFloatingButton()

    await router.push('/aiWork')
    await wrapper.vm.$nextTick()

    expect(wrapper.find('[data-testid="ai-work-floating"]').exists()).toBe(false)
  })

  describe('前台 presentation 临时接管', () => {
    it('presentation 期间展示只读运行界面：标题、状态与消息，隐藏 composer', async () => {
      const { wrapper, display } = await mountFloatingButton()
      attachPresentation(display, [
        {
          id: 'm1',
          role: 'assistant',
          parts: [{ type: 'text', text: '正在检索类目：ventilador' }],
        },
      ])

      await wrapper.get('[data-testid="ai-work-floating"]').trigger('mouseenter')

      const panel = wrapper.get('[data-testid="ai-work-floating-panel"]')
      expect(panel.attributes('aria-label')).toBe('前台 AI 任务只读展示')
      expect(panel.text()).toContain('AI 匹配类目')
      expect(panel.text()).toContain('conversation_presentation_x')
      expect(panel.text()).toContain('运行中')
      expect(panel.text()).toContain('正在检索类目：ventilador')
      // presentation 模式不渲染全局聊天面板与输入框。
      expect(panel.find('[data-testid="ai-chat-panel"]').exists()).toBe(false)
      expect(panel.find('[data-testid="ai-chat-input"]').exists()).toBe(false)
    })

    it('presentation 期间气泡 SPA 目标携带 presentation conversation 与 presentation_id', async () => {
      const { wrapper, display } = await mountFloatingButton()
      attachPresentation(display)

      await wrapper.vm.$nextTick()
      const link = wrapper.get('[data-testid="ai-work-floating-toggle"]')

      expect(link.attributes('href')).toBe(
        '/aiWork?conversation_id=conversation_presentation_x&presentation_id=presentation_x',
      )
    })

    it('presentation 接管不覆盖全局聊天：消息、输入与活动 Chat 保持不变', async () => {
      const { wrapper, store, display } = await mountFloatingButton()
      const conversationId = store.startConversation()
      store.input = '帮我看看草稿'

      attachPresentation(display)
      await wrapper.get('[data-testid="ai-work-floating"]').trigger('mouseenter')
      await wrapper.vm.$nextTick()

      expect(store.input).toBe('帮我看看草稿')
      expect(store.activeConversationId).toBe(conversationId)
      expect(store.chat?.id).toBe(conversationId)

      display.finishForegroundPresentation({ kind: 'success', text: '类目匹配完成' })
      await wrapper.vm.$nextTick()

      // 恢复后 global.chat 面板与输入原样出现。
      const panel = wrapper.get('[data-testid="ai-work-floating-panel"]')
      expect(panel.attributes('aria-label')).toBe('全局 AI 浮动对话')
      expect(panel.find('[data-testid="ai-chat-panel"]').exists()).toBe(true)
      expect(panel.find('[data-testid="ai-chat-input"]').exists()).toBe(true)
      expect(store.input).toBe('帮我看看草稿')
    })

    it('terminal 后浮窗恢复 global-chat 并短暂展示结果提示', async () => {
      const { wrapper, display } = await mountFloatingButton()
      attachPresentation(display)

      await wrapper.get('[data-testid="ai-work-floating"]').trigger('mouseenter')
      vi.useFakeTimers()
      try {
        display.finishForegroundPresentation({ kind: 'success', text: '类目匹配完成' })
        await wrapper.vm.$nextTick()

        const notice = wrapper.get('[data-testid="ai-work-terminal-notice"]')
        expect(notice.text()).toBe('类目匹配完成')
        expect(display.displayMode).toBe('global-chat')

        // 提示会自动清除，不永久遮挡 global.chat。
        vi.advanceTimersByTime(4500)
        await wrapper.vm.$nextTick()
        expect(display.terminalNotice).toBeNull()
      } finally {
        vi.useRealTimers()
      }
    })

    it('失败提示同样短暂展示后恢复 global-chat', async () => {
      const { wrapper, display } = await mountFloatingButton()
      attachPresentation(display)

      await wrapper.get('[data-testid="ai-work-floating"]').trigger('mouseenter')
      display.setForegroundError(new Error('AI Agent 运行失败。'))
      display.finishForegroundPresentation({ kind: 'failure', text: 'AI Agent 运行失败。' })
      await wrapper.vm.$nextTick()

      const notice = wrapper.get('[data-testid="ai-work-terminal-notice"]')
      expect(notice.text()).toBe('AI Agent 运行失败。')
      expect(display.foregroundPresentation).toBeNull()
    })
  })

  describe('展示连接中断的降级等待', () => {
    const DISPLAY_FAILURE_MESSAGE = '实时展示连接中断，正在等待业务结果…（Failed to fetch）'

    it('流中断后气泡保持 presentation 接管：显示中断提示、不提前恢复 global-chat', async () => {
      const { wrapper, store, display } = await mountFloatingButton()
      const conversationId = store.startConversation()
      store.input = '帮我看看草稿'
      attachPresentation(display, [
        {
          id: 'm1',
          role: 'assistant',
          parts: [{ type: 'text', text: '正在检索类目：ventilador' }],
        },
      ])

      // wrapper 在展示失败时写入降级错误并保持前台占用（业务结果仍在等待）。
      display.setForegroundError(new Error(DISPLAY_FAILURE_MESSAGE))

      await wrapper.get('[data-testid="ai-work-floating"]').trigger('mouseenter')

      const panel = wrapper.get('[data-testid="ai-work-floating-panel"]')
      expect(panel.attributes('aria-label')).toBe('前台 AI 任务只读展示')
      expect(wrapper.get('[data-testid="ai-work-presentation-error"]').text())
        .toContain('实时展示连接中断，正在等待业务结果…')
      // 已接收的消息继续展示；global-chat 与终态提示都不提前出现。
      expect(panel.text()).toContain('正在检索类目：ventilador')
      expect(panel.find('[data-testid="ai-chat-panel"]').exists()).toBe(false)
      expect(wrapper.find('[data-testid="ai-work-terminal-notice"]').exists()).toBe(false)
      expect(display.displayMode).toBe('presentation')
      // 全局聊天状态原样保留。
      expect(store.input).toBe('帮我看看草稿')
      expect(store.activeConversationId).toBe(conversationId)
      // 气泡入口仍指向当前 presentation。
      expect(wrapper.get('[data-testid="ai-work-floating-toggle"]').attributes('href'))
        .toBe('/aiWork?conversation_id=conversation_presentation_x&presentation_id=presentation_x')
    })

    it('首个事件前流中断：只显示中断提示，不显示误导性的“等待首个事件”空状态', async () => {
      const { wrapper, display } = await mountFloatingButton()
      attachPresentation(display)
      display.setForegroundError(new Error(DISPLAY_FAILURE_MESSAGE))

      await wrapper.get('[data-testid="ai-work-floating"]').trigger('mouseenter')

      expect(wrapper.find('[data-testid="ai-work-presentation-empty"]').exists()).toBe(false)
      expect(wrapper.get('[data-testid="ai-work-presentation-error"]').text())
        .toContain('实时展示连接中断')
    })

    it('降级等待拿到业务结果后恢复 global-chat 并短暂展示成功提示', async () => {
      const { wrapper, display } = await mountFloatingButton()
      attachPresentation(display)
      display.setForegroundError(new Error(DISPLAY_FAILURE_MESSAGE))

      await wrapper.get('[data-testid="ai-work-floating"]').trigger('mouseenter')
      vi.useFakeTimers()
      try {
        // 业务 response 返回成功：wrapper 收尾并恢复全局聊天。
        display.finishForegroundPresentation({ kind: 'success', text: '类目匹配完成' })
        await wrapper.vm.$nextTick()

        const panel = wrapper.get('[data-testid="ai-work-floating-panel"]')
        expect(panel.attributes('aria-label')).toBe('全局 AI 浮动对话')
        expect(panel.find('[data-testid="ai-chat-panel"]').exists()).toBe(true)
        expect(wrapper.get('[data-testid="ai-work-terminal-notice"]').text())
          .toBe('类目匹配完成')

        vi.advanceTimersByTime(4500)
        await wrapper.vm.$nextTick()
        expect(display.terminalNotice).toBeNull()
      } finally {
        vi.useRealTimers()
      }
    })
  })
})
