import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { defineComponent } from 'vue'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { Chat } from '@ai-sdk/vue'
import type { UIMessage } from 'ai'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useAiChatStore, useAiWorkDisplayStore } from '@/stores'
import { AI_CHAT_SELECTION_KEY } from '@/stores/aiChat'
import AiChatPanel from '@/components/ai-work/AiChatPanel.vue'
import AiWorkFloatingButton from '../AiWorkFloatingButton.vue'

enableAutoUnmount(afterEach)

function dispatchPointer(type: string, clientX: number, clientY: number, pointerId = 1) {
  window.dispatchEvent(Object.assign(new MouseEvent(type, { clientX, clientY, cancelable: true }), {
    pointerId, isPrimary: true,
  }))
}

const mocks = vi.hoisted(() => ({
  fetchUiMessages: vi.fn(),
}))

vi.mock('@/api/aiWork', () => ({
  AI_CHAT_RUNS_PATH: '/api/v1/ai-chat/runs',
  conversationEventsUrl: (conversationId: string, afterHistoryVersion: number) => (
    `/api/v1/ai-work/conversations/${conversationId}/events`
    + `?after_history_version=${Math.max(0, Math.floor(afterHistoryVersion))}`
  ),
  fetchUiMessages: mocks.fetchUiMessages,
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
    attachTo: document.body,
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
    mocks.fetchUiMessages.mockReset()
    localStorage.removeItem(AI_CHAT_SELECTION_KEY)
  })

  it('恢复失败显示重试，成功后继续原会话；只有新对话按钮清空选择', async () => {
    const id = `conversation_global_chat_${'ab'.repeat(16)}`
    const saved = new Map([[AI_CHAT_SELECTION_KEY, id]])
    const get = vi.spyOn(localStorage, 'getItem').mockImplementation(key => saved.get(key) ?? null)
    const set = vi.spyOn(localStorage, 'setItem').mockImplementation((key, value) => { saved.set(key, value) })
    const remove = vi.spyOn(localStorage, 'removeItem').mockImplementation(key => { saved.delete(key) })
    mocks.fetchUiMessages.mockRejectedValueOnce(new Error('网络中断')).mockResolvedValue({
      ok: true, conversation_id: id, history_version: 1,
      messages: [{ id: 'old', role: 'user', parts: [{ type: 'text', text: '之前的问题' }] }],
    })
    try {
      const { wrapper, store } = await mountFloatingButton()
      await wrapper.get('[data-testid="ai-work-floating"]').trigger('mouseenter')
      await flushPromises()
      expect(wrapper.get('[data-testid="ai-chat-restore-error"]').text()).toContain('网络中断')
      expect(wrapper.find('[data-testid="ai-chat-input"]').exists()).toBe(false)
      expect(wrapper.find('[data-testid="ai-chat-empty"]').exists()).toBe(false)
      await wrapper.get('[data-testid="ai-chat-restore-error"] button').trigger('click')
      await flushPromises()
      expect(store.activeConversationId).toBe(id)
      expect(wrapper.get('[data-testid="ai-chat-panel"]').text()).toContain('之前的问题')
      await wrapper.get('[data-testid="ai-work-new-chat"]').trigger('click')
      expect(store.activeConversationId).not.toBe(id)
      expect(saved.has(AI_CHAT_SELECTION_KEY)).toBe(false)
      expect(wrapper.find('[data-testid="ai-chat-empty"]').exists()).toBe(true)
      store.disconnectEvents()
    } finally { get.mockRestore(); set.mockRestore(); remove.mockRestore() }
  })

  it('拖拽气泡移动位置并阻止本次跳转，下一次普通点击仍能进入 AIWork', async () => {
    const { wrapper, router } = await mountFloatingButton()
    const link = wrapper.get('[data-testid="ai-work-floating-toggle"]')
    const floating = wrapper.get('[data-testid="ai-work-floating"]')
    await link.trigger('pointerdown', { isPrimary: true, button: 0, pointerId: 1, clientX: 200, clientY: 200 })
    dispatchPointer('pointermove', 100, 150)
    dispatchPointer('pointerup', 100, 150)
    link.element.dispatchEvent(new MouseEvent('click', { detail: 1, bubbles: true, cancelable: true }))
    await flushPromises()
    expect((floating.element as HTMLElement).style.right).toBe('120px')
    expect((floating.element as HTMLElement).style.bottom).toBe('70px')
    expect(router.currentRoute.value.name).toBe('WorkflowHome')

    await link.trigger('pointerdown', { isPrimary: true, button: 0, pointerId: 2, clientX: 100, clientY: 150 })
    dispatchPointer('pointermove', 102, 151, 2)
    dispatchPointer('pointerup', 102, 151, 2)
    link.element.dispatchEvent(new MouseEvent('click', { detail: 1, bubbles: true, cancelable: true }))
    await flushPromises()
    expect(router.currentRoute.value.name).toBe('AiWork')
  })

  it('展开的面板不限制气泡拖拽，拖动时隐藏面板，取消后停止移动并恢复展示', async () => {
    const { wrapper } = await mountFloatingButton()
    const link = wrapper.get('[data-testid="ai-work-floating-toggle"]')
    const floating = wrapper.get('[data-testid="ai-work-floating"]')
    await floating.trigger('mouseenter')
    const rect = vi.spyOn(floating.element, 'getBoundingClientRect').mockReturnValue({ width: 56, height: 56 } as DOMRect)
    const placement = wrapper.get('[data-testid="ai-work-floating-placement"]')
    const panel = wrapper.get('[data-testid="ai-work-floating-panel"]')
    vi.spyOn(panel.element, 'getBoundingClientRect').mockReturnValue({ width: 400, height: 560 } as DOMRect)
    try {
      await link.trigger('pointerdown', { isPrimary: true, button: 0, pointerId: 1, clientX: 200, clientY: 200 })
      dispatchPointer('pointermove', -5000, -5000)
      await wrapper.vm.$nextTick()
      const style = (floating.element as HTMLElement).style
      expect(style.right).toBe(`${window.innerWidth - 76}px`)
      expect(style.bottom).toBe(`${window.innerHeight - 76}px`)
      expect(placement.isVisible()).toBe(false)
      expect(link.attributes('aria-expanded')).toBe('false')
      dispatchPointer('pointermove', 5000, 5000)
      await wrapper.vm.$nextTick()
      expect(style.right).toBe('20px')
      expect(style.bottom).toBe('20px')
      dispatchPointer('pointercancel', 5000, 5000)
      dispatchPointer('pointermove', 100, 100)
      await wrapper.vm.$nextTick()
      expect(style.right).toBe('20px')
      expect(style.bottom).toBe('20px')
      expect(placement.isVisible()).toBe(true)
      expect(link.attributes('aria-expanded')).toBe('true')
    } finally { rect.mockRestore() }
  })

  it('气泡拖到左上方后向下展开，重复悬停不改变气泡位置', async () => {
    const { wrapper } = await mountFloatingButton()
    const floating = wrapper.get('[data-testid="ai-work-floating"]')
    const link = wrapper.get('[data-testid="ai-work-floating-toggle"]')
    await link.trigger('pointerdown', { isPrimary: true, button: 0, pointerId: 1, clientX: 200, clientY: 200 })
    dispatchPointer('pointermove', -5000, -5000)
    dispatchPointer('pointerup', -5000, -5000)
    await wrapper.vm.$nextTick()
    const position = floating.attributes('style')
    await floating.trigger('mouseenter')
    expect(wrapper.get('[data-testid="ai-work-floating-placement"]').attributes('data-side')).toBe('bottom')
    expect(floating.attributes('style')).toBe(position)
    await floating.trigger('mouseleave')
    await floating.trigger('mouseenter')
    expect(floating.attributes('style')).toBe(position)
  })

  it('默认渲染 SPA 链接，指向 /aiWork 且不携带 target/rel', async () => {
    const { wrapper } = await mountFloatingButton()

    const link = wrapper.get('[data-testid="ai-work-floating-toggle"]')
    expect(link.element.tagName).toBe('A')
    expect(link.attributes('href')).toBe('/aiWork')
    expect(link.attributes('target')).toBeUndefined()
    expect(link.attributes('rel')).toBeUndefined()
    expect(link.attributes('aria-label')).toBe('打开 AI 助手')
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
    expect(panel.attributes('aria-label')).toBe('AI 助手')
    expect(panel.find('[data-testid="ai-chat-panel"]').exists()).toBe(true)
    expect(panel.find('[data-testid="ai-chat-input"]').exists()).toBe(true)

    await floating.trigger('mouseleave')
    expect(store.floatingOpen).toBe(false)
    expect(wrapper.find('[role="region"]').exists()).toBe(false)
  })

  it('/resume 打开期间转移焦点和鼠标保留聊天，关闭后恢复自动收起', async () => {
    const { wrapper, store } = await mountFloatingButton()
    const floating = wrapper.get('[data-testid="ai-work-floating"]')
    await floating.trigger('mouseenter')
    await floating.trigger('focusin')
    store.input = '/resume'
    store.sendMessage()
    await floating.trigger('mouseleave')
    await floating.trigger('focusout', { relatedTarget: document.body })
    expect(store.resumeOpen).toBe(true)
    expect(wrapper.find('[data-testid="ai-work-floating-panel"]').exists()).toBe(true)

    store.resumeOpen = false
    await floating.trigger('mouseenter')
    await floating.trigger('mouseleave')
    expect(store.floatingOpen).toBe(false)
  })

  it('图钉位于关闭按钮左侧，钉住后鼠标和焦点移出都不收起', async () => {
    const { wrapper, store } = await mountFloatingButton()
    const floating = wrapper.get('[data-testid="ai-work-floating"]')
    await floating.trigger('mouseenter')

    const panel = wrapper.get('[data-testid="ai-work-floating-panel"]')
    const headerButtons = panel.get('header').findAll('button')
    expect(headerButtons.map((button) => button.attributes('data-testid'))).toEqual([
      'ai-work-new-chat',
      'ai-work-floating-pin',
      'ai-work-floating-close',
    ])

    const pin = wrapper.get('[data-testid="ai-work-floating-pin"]')
    expect(pin.attributes('aria-pressed')).toBe('false')
    await pin.trigger('click')
    expect(pin.attributes('aria-pressed')).toBe('true')

    await floating.trigger('mouseleave')
    await floating.trigger('focusout', { relatedTarget: document.body })
    expect(store.floatingOpen).toBe(true)
    expect(wrapper.find('[data-testid="ai-work-floating-panel"]').exists()).toBe(true)

    await pin.trigger('click')
    expect(pin.attributes('aria-pressed')).toBe('false')
    await floating.trigger('mouseleave')
    expect(store.floatingOpen).toBe(false)
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

  it('浮层面板把 conversation id 传给 AiChatPanel 并保留可用输入框', async () => {
    const { wrapper, store } = await mountFloatingButton()
    const conversationId = store.startConversation()
    await wrapper.get('[data-testid="ai-work-floating"]').trigger('mouseenter')

    const panel = wrapper.findComponent(AiChatPanel)
    expect(panel.exists()).toBe(true)
    expect(panel.props('conversationId')).toBe(conversationId)

    expect(wrapper.find('[data-testid="ai-chat-input"]').attributes('disabled')).toBeUndefined()

    wrapper.unmount()
  })

  it('关闭按钮收起面板', async () => {
    const { wrapper, store } = await mountFloatingButton()

    await wrapper.get('[data-testid="ai-work-floating"]').trigger('mouseenter')
    expect(wrapper.find('[role="region"]').exists()).toBe(true)

    await wrapper.get('[data-testid="ai-work-floating-pin"]').trigger('click')
    await wrapper.get('[data-testid="ai-work-floating-close"]').trigger('click')
    expect(store.floatingOpen).toBe(false)
    expect(wrapper.find('[role="region"]').exists()).toBe(false)
  })

  it('已有消息的气泡链接携带 conversation query，且不再是新标签页链接', async () => {
    const { wrapper, store } = await mountFloatingButton()
    const conversationId = store.startConversation()
    store.chat!.messages = [{ id: 'user', role: 'user', parts: [{ type: 'text', text: '原问题' }] }]

    await wrapper.vm.$nextTick()
    const link = wrapper.get('[data-testid="ai-work-floating-toggle"]')

    expect(link.attributes('href')).toBe(`/aiWork?conversation_id=${conversationId}`)
    expect(link.attributes('target')).toBeUndefined()
    expect(link.attributes('rel')).toBeUndefined()
  })

  it('普通点击在当前标签页 SPA 导航，不清空 store 也不停止流', async () => {
    const { wrapper, store, router } = await mountFloatingButton()
    const conversationId = store.startConversation()
    store.chat!.messages = [{ id: 'user', role: 'user', parts: [{ type: 'text', text: '原问题' }] }]
    store.input = '帮我看看草稿'
    const stopSpy = vi.spyOn(store, 'stopStreaming')
    const newConversationSpy = vi.spyOn(store, 'startConversation')

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

  describe('任务进度与普通聊天独立', () => {
    it('任务开始、结束均保留同一个聊天面板和输入，任务详情有独立入口', async () => {
      const { wrapper, store, display } = await mountFloatingButton()
      const id = store.startConversation()
      store.chat!.messages = [{ id: 'user', role: 'user', parts: [{ type: 'text', text: '原问题' }] }]
      store.input = '继续原来的问题'
      await wrapper.get('[data-testid="ai-work-floating"]').trigger('mouseenter')
      const panel = wrapper.findComponent(AiChatPanel).element
      attachPresentation(display)
      await wrapper.vm.$nextTick()
      expect(wrapper.findComponent(AiChatPanel).element === panel).toBe(true)
      expect(wrapper.get('[data-testid="ai-chat-input"]').element).toHaveProperty('value', '继续原来的问题')
      expect(wrapper.get('[data-testid="ai-work-task-progress"]').text()).toContain('AI 匹配类目')
      expect(wrapper.get('[data-testid="ai-work-floating-toggle"]').attributes('href')).toBe(`/aiWork?conversation_id=${id}`)
      expect(wrapper.get('[data-testid="ai-work-task-details"]').attributes('href'))
        .toBe('/aiWork?conversation_id=conversation_presentation_x&presentation_id=presentation_x')
      display.finishForegroundPresentation({ kind: 'success', text: '类目匹配完成' })
      await wrapper.vm.$nextTick()
      expect(wrapper.findComponent(AiChatPanel).element === panel).toBe(true)
      expect(wrapper.find('[data-testid="ai-work-task-progress"]').exists()).toBe(false)
      expect(wrapper.get('[data-testid="ai-work-terminal-notice"]').text()).toBe('类目匹配完成')
      expect(store.activeConversationId).toBe(id)
      expect(store.input).toBe('继续原来的问题')
    })

    it('任务流中断只在进度卡提示，不隐藏聊天输入框', async () => {
      const { wrapper, display } = await mountFloatingButton()
      attachPresentation(display)
      display.setForegroundError(new Error('实时展示连接中断，正在等待业务结果…'))
      await wrapper.get('[data-testid="ai-work-floating"]').trigger('mouseenter')
      expect(wrapper.get('[data-testid="ai-work-presentation-error"]').text()).toContain('实时展示连接中断')
      expect(wrapper.find('[data-testid="ai-chat-input"]').exists()).toBe(true)
      expect(wrapper.get('[data-testid="ai-work-floating-toggle"]').attributes('href')).toBe('/aiWork')
    })

    it.each(['success', 'failure'] as const)('任务 %s 结果提示自动清除', async (kind) => {
      const { wrapper, display } = await mountFloatingButton()
      attachPresentation(display)
      await wrapper.get('[data-testid="ai-work-floating"]').trigger('mouseenter')
      vi.useFakeTimers()
      try {
        display.finishForegroundPresentation({ kind, text: '任务结果' })
        await wrapper.vm.$nextTick()
        expect(wrapper.get('[data-testid="ai-work-terminal-notice"]').text()).toBe('任务结果')
        vi.advanceTimersByTime(4500)
        await wrapper.vm.$nextTick()
        expect(display.terminalNotice).toBeNull()
      } finally { vi.useRealTimers() }
    })
  })
})
