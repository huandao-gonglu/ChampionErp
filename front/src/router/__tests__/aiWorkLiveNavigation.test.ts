import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter, RouterView, type Router } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import AiWorkFloatingButton from '@/components/common/AiWorkFloatingButton.vue'
import AiWorkView from '@/views/AiWorkView.vue'
import { useAiChatStore } from '@/stores'

const mocks = vi.hoisted(() => ({
  fetchConversations: vi.fn(),
  fetchConversation: vi.fn(),
  fetchUiMessages: vi.fn(),
}))

vi.mock('@/api/aiWork', () => ({
  fetchPydanticConversations: mocks.fetchConversations,
  fetchPydanticConversation: mocks.fetchConversation,
  fetchUiMessages: mocks.fetchUiMessages,
  AI_CHAT_RUNS_PATH: '/api/v1/ai-chat/runs',
}))

/** 模拟 App.vue 的挂载方式：RouterView 之外挂载浮动入口，路由切换不销毁 store owner。 */
const AppHost = defineComponent({
  render: () => [h(RouterView), h(AiWorkFloatingButton)],
})

const encoder = new TextEncoder()

function sseChunk(payload: Record<string, unknown> | '[DONE]'): Uint8Array {
  const value = payload === '[DONE]' ? payload : JSON.stringify(payload)
  return encoder.encode(`data: ${value}\n\n`)
}

describe('浮动气泡同标签页导航到 AiWork 的实时流集成', () => {
  let controller: ReadableStreamDefaultController<Uint8Array> | undefined
  let runFetchCount = 0

  beforeEach(() => {
    vi.clearAllMocks()
    controller = undefined
    runFetchCount = 0
    mocks.fetchConversations.mockResolvedValue({ ok: true, conversations: [] })
    mocks.fetchConversation.mockImplementation(async (conversationId: string) => ({
      ok: true,
      conversation_id: conversationId,
      created_at: '2026-08-16T08:00:00+08:00',
      updated_at: '2026-08-16T08:05:00+08:00',
      messages: [],
    }))
    mocks.fetchUiMessages.mockImplementation(async (conversationId: string) => ({
      ok: true,
      conversation_id: conversationId,
      // 完成流之后以服务端已提交历史对齐游标：回显当前 Chat 消息，
      // 保证 id/内容与流式结果一致。
      history_version: 1,
      created_at: '2026-08-16T08:00:00+08:00',
      updated_at: '2026-08-16T08:05:00+08:00',
      messages: [...(useAiChatStore().chat?.messages ?? [])],
    }))

    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/api/v1/ai-chat/runs')) {
        runFetchCount += 1
        return new Response(
          new ReadableStream<Uint8Array>({
            start(value) {
              controller = value
            },
          }),
          {
            status: 200,
            headers: {
              'Content-Type': 'text/event-stream',
              'x-vercel-ai-ui-message-stream': 'v1',
            },
          },
        )
      }
      return new Response('{}', { status: 404 })
    })
    vi.stubGlobal('fetch', fetchMock)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  async function setupApp(): Promise<{
    wrapper: ReturnType<typeof mount>
    router: Router
    store: ReturnType<typeof useAiChatStore>
  }> {
    const pinia = createPinia()
    setActivePinia(pinia)
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        {
          path: '/',
          name: 'WorkflowHome',
          component: { render: () => h('div', { 'data-testid': 'business-page' }) },
        },
        { path: '/aiWork', name: 'AiWork', component: AiWorkView },
      ],
    })
    await router.push('/')
    await router.isReady()
    const wrapper = mount(AppHost, { global: { plugins: [pinia, router] } })
    return { wrapper, router, store: useAiChatStore() }
  }

  it('流式过程中点击进入 AiWork：同一 Chat、一次 POST，导航后继续合并增量', async () => {
    const { wrapper, router, store } = await setupApp()

    // 1. 业务页面通过浮动面板发送消息，开始唯一一次 SSE。
    await wrapper.get('[data-testid="ai-work-floating"]').trigger('mouseenter')
    await wrapper.get('[data-testid="ai-chat-input"]').setValue('查询草稿')
    await wrapper.get('[data-testid="ai-chat-composer"]').trigger('submit')

    await vi.waitFor(() => {
      expect(controller).toBeDefined()
      expect(store.status).toBe('submitted')
    })
    const conversationId = store.activeConversationId
    expect(conversationId).toBeTruthy()

    // 2. 接收第一段 assistant delta。
    controller!.enqueue(sseChunk({ type: 'start', messageId: 'assistant-1' }))
    controller!.enqueue(sseChunk({ type: 'start-step' }))
    controller!.enqueue(sseChunk({ type: 'text-start', id: 'text-1' }))
    controller!.enqueue(sseChunk({ type: 'text-delta', id: 'text-1', delta: '第一段' }))
    await vi.waitFor(() => {
      expect(store.status).toBe('streaming')
      expect(store.messages).toHaveLength(2)
    })

    const chatBefore = store.chat
    const userMessageId = store.messages[0]?.id
    const assistantMessageId = store.messages[1]?.id

    // 3. 普通点击气泡：当前标签页 SPA 导航到 AiWork。
    await wrapper.get('[data-testid="ai-work-floating-toggle"]').trigger('click')
    await vi.waitFor(() => {
      expect(router.currentRoute.value.name).toBe('AiWork')
    })
    await flushPromises()

    // 4. route 与 conversation query 正确。
    expect(router.currentRoute.value.path).toBe('/aiWork')
    expect(router.currentRoute.value.query.conversation_id).toBe(conversationId)

    // 5. Chat 对象、conversation ID 与已接收消息保持不变。
    expect(store.chat).toBe(chatBefore)
    expect(store.activeConversationId).toBe(conversationId)
    expect(store.messages).toHaveLength(2)
    expect(store.messages[0]?.id).toBe(userMessageId)
    expect(store.messages[1]?.id).toBe(assistantMessageId)

    // AiWork 立即绑定活动会话：浮动入口隐藏，实时区域接管且不请求派生接口。
    expect(wrapper.find('[data-testid="ai-work-floating"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="ai-work-live-chat"]').exists()).toBe(true)
    expect(wrapper.get('[data-testid="ai-work-selected-id"]').text()).toBe(conversationId)
    expect(mocks.fetchUiMessages).not.toHaveBeenCalled()
    expect(wrapper.get('[data-testid="ai-work-live-chat"]').text()).toContain('第一段')

    // 6. 导航后继续写入第二段 delta。
    controller!.enqueue(sseChunk({ type: 'text-delta', id: 'text-1', delta: '第二段' }))

    // 7. AiWork 同一 assistant 气泡显示两段合并文本。
    await vi.waitFor(() => {
      const live = wrapper.get('[data-testid="ai-work-live-chat"]')
      expect(live.text()).toContain('第一段第二段')
    })
    expect(store.messages[1]?.id).toBe(assistantMessageId)

    // 8. /api/v1/ai-chat/runs 只调用一次。
    expect(runFetchCount).toBe(1)

    // 9. 完成流：historyVersion 只增加一次，左侧列表只多刷新一次。
    controller!.enqueue(sseChunk({ type: 'text-end', id: 'text-1' }))
    controller!.enqueue(sseChunk({ type: 'finish-step' }))
    controller!.enqueue(sseChunk({ type: 'finish', finishReason: 'stop' }))
    controller!.enqueue(sseChunk('[DONE]'))
    controller!.close()
    await vi.waitFor(() => {
      expect(store.status).toBe('ready')
      expect(store.historyVersion).toBe(1)
    })
    await vi.waitFor(() => {
      // 进入 AiWork 时列表请求一次 + 完成后刷新一次。
      expect(mocks.fetchConversations).toHaveBeenCalledTimes(2)
    })

    // 10. 返回业务页面，再次悬停仍显示同一 conversation 的完整消息。
    await router.push('/')
    await flushPromises()
    expect(wrapper.find('[data-testid="ai-work-floating"]').exists()).toBe(true)
    expect(store.chat).toBe(chatBefore)

    await wrapper.get('[data-testid="ai-work-floating"]').trigger('mouseenter')
    const panel = wrapper.get('[data-testid="ai-work-floating-panel"]')
    expect(panel.text()).toContain('查询草稿')
    expect(panel.text()).toContain('第一段第二段')
    expect(panel.text()).toContain(conversationId || '')
  })
})
