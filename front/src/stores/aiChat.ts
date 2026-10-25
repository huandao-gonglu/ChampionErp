import { computed, ref, shallowRef, nextTick } from 'vue'
import { defineStore } from 'pinia'
import { Chat } from '@ai-sdk/vue'
import { DefaultChatTransport } from 'ai'
import type { UIMessage } from 'ai'
import { AI_CHAT_RUNS_PATH, cancelChatRun, conversationEventsUrl, fetchUiMessages, forkChatConversation } from '@/api/aiWork'
import { apiClient } from '@/api/client'
import { matchChatCommand } from '@/services/chatCommands'
import type { ChatCommandContext } from '@/services/chatCommands'
import { GLOBAL_CHAT_CONVERSATION_PREFIX } from '@/types/aiWork'
import type { AiWorkUiMessagesResponse, PendingToolCall } from '@/types/aiWork'
import { useAiPageContextStore } from './aiPageContext'

export const AI_CHAT_TURN_ALREADY_ACCEPTED = 'AI_CHAT_TURN_ALREADY_ACCEPTED'
export const AI_CHAT_SELECTION_KEY = 'ai-chat-selected-conversation'
export interface AiChatError extends Error { code?: string; status?: number }
export function createChatConversationId(): string {
  return GLOBAL_CHAT_CONVERSATION_PREFIX + crypto.randomUUID().replace(/-/g, '')
}

export const useAiChatStore = defineStore('aiChat', () => {
  const pageContext = useAiPageContextStore()
  const activeConversationId = ref<string | null>(null)
  const chat = shallowRef<Chat<UIMessage> | null>(null)
  const input = ref('')
  const floatingOpen = ref(false)
  const restoring = ref(false)
  const restoreError = ref('')
  let restored = false
  let restorePromise: Promise<void> | undefined
  const historyVersion = ref(0)
  const stopping = ref(false)
  const forking = ref(false)
  const localTurnStartedAt = ref('')
  const runActive = ref(false)
  const latestMessageId = ref<string | null>(null)
  const pendingToolCalls = ref<PendingToolCall[]>([])
  const receivedNotice = ref('')
  const localError = ref<AiChatError>()
  const targetDraftIds = ref<string[]>([])
  const receivedMessages = ref<{ message_id: string; text: string }[]>([])
  let eventSource: EventSource | null = null
  let retryTimer: ReturnType<typeof setTimeout> | undefined
  let generation = 0
  let stopRequestVersion = 0
  const pendingInputIds = new Set<string>()
  const status = computed(() => chat.value?.status ?? 'ready')
  const conversationLinkId = computed(() => (
    historyVersion.value > 0 || chat.value?.messages.length ? activeConversationId.value : null
  ))
  const isBusy = computed(() => status.value === 'submitted' || status.value === 'streaming')
  const isRunning = computed(() => isBusy.value || runActive.value)
  const messages = computed<UIMessage[]>(() => {
    const result: UIMessage[] = JSON.parse(JSON.stringify(chat.value?.messages ?? []))
    const latestUser = result.slice().reverse().find(message => message.role === 'user')
    if (latestUser && localTurnStartedAt.value) {
      const metadata = (latestUser.metadata ?? {}) as Record<string, unknown>
      if (!metadata.ai_turn) latestUser.metadata = { ...metadata, ai_turn: { started_at: localTurnStartedAt.value } }
    }
    return result
  })
  const error = computed(() => localError.value ?? chat.value?.error as AiChatError | undefined)
  const canSend = computed(() => Boolean(input.value.trim()) && !stopping.value && !forking.value && !restoring.value && !restoreError.value)
  const canStop = computed(() => isBusy.value || runActive.value || pendingToolCalls.value.length > 0 || stopping.value)

  // 只记住选择，不复制消息；消息与活动运行始终从服务端恢复。
  function rememberConversation(id: string | null): void {
    try {
      if (id) localStorage.setItem(AI_CHAT_SELECTION_KEY, id)
      else localStorage.removeItem(AI_CHAT_SELECTION_KEY)
    } catch { /* 存储不可用时仍允许当前标签页继续对话。 */ }
  }
  function finishRestore(): void {
    restored = true
    restoring.value = false
    restoreError.value = ''
  }
  function restoreConversation(): Promise<void> {
    if (restored || chat.value) return Promise.resolve()
    if (restoring.value && restorePromise) return restorePromise
    let id: string | null = null
    try { id = localStorage.getItem(AI_CHAT_SELECTION_KEY) } catch { /* 无可恢复选择。 */ }
    if (!id?.startsWith(GLOBAL_CHAT_CONVERSATION_PREFIX)) {
      finishRestore()
      return Promise.resolve()
    }
    const requestGeneration = ++generation
    restoring.value = true
    restoreError.value = ''
    restorePromise = (async () => {
      try {
        const detail = await fetchUiMessages(id)
        if (requestGeneration !== generation) return
        if (!detail?.ok || detail.conversation_id !== id || !Array.isArray(detail.messages)) {
          throw new Error('返回的对话记录不完整或与上次选择不一致')
        }
        openConversation(detail)
      } catch (cause) {
        if (requestGeneration !== generation) return
        restoreError.value = `恢复上次对话失败：${cause instanceof Error ? cause.message : String(cause)}`
      } finally {
        if (requestGeneration === generation) restoring.value = false
      }
    })()
    return restorePromise
  }

  function disconnectEvents(): void {
    eventSource?.close()
    eventSource = null
    if (retryTimer) clearTimeout(retryTimer)
    retryTimer = undefined
  }
  function connectEvents(conversationId: string): void {
    if (activeConversationId.value !== conversationId) return
    disconnectEvents()
    if (typeof EventSource === 'undefined') return
    const source = new EventSource(conversationEventsUrl(conversationId, historyVersion.value))
    eventSource = source
    source.onmessage = () => {
      if (eventSource !== source) return
      source.close()
      if (isBusy.value) return
      void resyncFromServer(conversationId)
    }
    source.onerror = () => {
      source.close()
      if (eventSource !== source) return
      retryTimer = setTimeout(() => void resyncFromServer(conversationId), 1000)
    }
  }
  async function resyncFromServer(conversationId: string, attempt = 0): Promise<void> {
    if (activeConversationId.value !== conversationId) return
    if (isBusy.value) {
      if (stopping.value) retryTimer = setTimeout(() => void resyncFromServer(conversationId), 500)
      return
    }
    const requestGeneration = ++generation
    try {
      const detail = await fetchUiMessages(conversationId)
      if (requestGeneration !== generation || chat.value?.id !== conversationId) return
      if (detail.history_version < historyVersion.value) return
      runActive.value = detail.run_active ?? false
      if (!pendingInputIds.size) latestMessageId.value = detail.latest_message_id ?? latestMessageId.value
      if (runActive.value && detail.run_status === 'cancelled') stopping.value = true
      if (stopping.value && runActive.value) {
        retryTimer = setTimeout(() => void resyncFromServer(conversationId), 2000)
        return
      }
      stopping.value = false
      chat.value.messages = detail.messages
      historyVersion.value = detail.history_version
      pendingToolCalls.value = detail.pending_tool_calls ?? []
      localError.value = detail.run_error ? Object.assign(new Error(detail.run_error.message), { code: detail.run_error.code }) : undefined
      const pending = new Set((detail.received_messages ?? []).map(row => row.message_id))
      receivedMessages.value = receivedMessages.value.filter(row => pending.has(row.message_id))
      receivedNotice.value = pending.size ? '已收到，等待当前操作结束后应用' : ''
      connectEvents(conversationId)
      if (runActive.value && typeof EventSource === 'undefined') retryTimer = setTimeout(() => void resyncFromServer(conversationId), 2000)
    } catch {
      if (requestGeneration !== generation || activeConversationId.value !== conversationId) return
      if (attempt < 3) retryTimer = setTimeout(() => void resyncFromServer(conversationId, attempt + 1), 500 * 2 ** attempt)
      else {
        if (stopping.value) {
          stopping.value = false
          receivedNotice.value = '停止请求已发送，暂时无法确认结果，请重试'
        }
        connectEvents(conversationId)
      }
    }
  }
  async function chatFetch(url: RequestInfo | URL, init?: RequestInit): Promise<Response> {
    const response = await fetch(url, init)
    if (response.status === 202) {
      const receipt = await response.json()
      if (receipt.conversation_id === activeConversationId.value) receivedNotice.value = receipt.message
      const accepted = new Error(receipt.message) as AiChatError
      accepted.code = 'AI_CHAT_INPUT_ACCEPTED'
      throw accepted
    }
    if (response.ok) return response
    const payload = await response.json().catch(() => ({}))
    const failure = new Error(payload.error ?? `HTTP ${response.status}`) as AiChatError
    failure.code = payload.error_code
    failure.status = response.status
    throw failure
  }
  function createChat(conversationId: string, initial: UIMessage[]): Chat<UIMessage> {
    const instance = new Chat<UIMessage>({
      id: conversationId, messages: initial,
      transport: new DefaultChatTransport({
        api: AI_CHAT_RUNS_PATH, credentials: 'same-origin', fetch: chatFetch,
        prepareSendMessagesRequest: async ({ id, messages: all, trigger, body }) => {
          const latest = all.at(-1)
          if (latest?.role === 'assistant') {
            const parts = latest.parts.filter(part => 'state' in part && part.state === 'approval-responded')
            const { data } = await apiClient.get<{ approvalToken: string }>('/api/state')
            return { headers: { 'X-Approval-Token': data.approvalToken }, body: { id, trigger, messages: [{ ...latest, parts }] } }
          }
          if (latest?.role === 'user' && activeConversationId.value === id) latestMessageId.value = latest.id
          return { body: { id, trigger, messages: latest ? [latest] : [], ...body } }
        },
      }),
      onFinish: () => {
        if (!stopping.value) void nextTick().then(() => resyncFromServer(conversationId))
      },
      onError: (cause) => {
        const code = (cause as AiChatError).code
        if (code === 'AI_CHAT_INPUT_ACCEPTED' || code === AI_CHAT_TURN_ALREADY_ACCEPTED) {
          void nextTick().then(() => { instance.clearError(); void resyncFromServer(conversationId) })
        } else {
          connectEvents(conversationId)
        }
      },
    })
    return instance
  }
  function startConversation(): string {
    finishRestore()
    // 未发送的空白对话不在服务端存在；刷新后继续保持空白，不恢复上一段。
    rememberConversation(null)
    localTurnStartedAt.value = ''
    disconnectEvents()
    generation++
    stopRequestVersion++
    const id = createChatConversationId()
    activeConversationId.value = id
    historyVersion.value = 0
    pendingToolCalls.value = []
    receivedMessages.value = []
    receivedNotice.value = ''
    targetDraftIds.value = []
    localError.value = undefined
    stopping.value = false
    runActive.value = false
    latestMessageId.value = null
    chat.value = createChat(id, [])
    connectEvents(id)
    return id
  }
  async function queueText(text: string): Promise<void> {
    const id = activeConversationId.value
    if (!id) return
    const messageId = crypto.randomUUID()
    latestMessageId.value = messageId
    const requestVersion = stopRequestVersion
    pendingInputIds.add(messageId)
    let response: Response
    try {
      response = await fetch(AI_CHAT_RUNS_PATH, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({
        id, trigger: 'submit-message', messages: [{ id: messageId, role: 'user', parts: [{ type: 'text', text }] }],
        target_draft_ids: targetDraftIds.value,
        page_context: pageContext.snapshot(),
      }) })
    } catch (cause) {
      if (requestVersion !== stopRequestVersion) return
      throw cause
    } finally {
      pendingInputIds.delete(messageId)
    }
    if (requestVersion !== stopRequestVersion || stopping.value) { await response.body?.cancel(); return }
    if (!response.ok) throw new Error((await response.json()).error ?? '消息未能提交')
    if (activeConversationId.value !== id) { await response.body?.cancel(); return }
    receivedMessages.value.push({ message_id: messageId, text })
    receivedNotice.value = response.status === 202 ? (await response.json()).message : '已收到，正在应用'
    if (response.status !== 202) await response.body?.cancel()
    connectEvents(id)
  }
  function commandContext(): ChatCommandContext {
    return { isBusy: canStop.value, startConversation, stopStreaming, refreshHistory }
  }
  function sendMessage(): void {
    if (stopping.value || forking.value) return
    if (!restored && !chat.value) void restoreConversation()
    if (restoring.value || restoreError.value) return
    const text = input.value.trim()
    if (!text) return
    const command = matchChatCommand(text)
    if (command && command.command.available(commandContext())) {
      if (command.command.execute(commandContext(), command.arg)) input.value = ''
      return
    }
    if (!chat.value) startConversation()
    rememberConversation(activeConversationId.value)
    input.value = ''
    localError.value = undefined
    if (isBusy.value || runActive.value || pendingToolCalls.value.length) {
      void queueText(text).catch(cause => { localError.value = cause; input.value = text })
    } else {
      localTurnStartedAt.value = new Date().toISOString()
      void chat.value?.sendMessage({ text }, { body: {
        target_draft_ids: [...targetDraftIds.value],
        page_context: pageContext.snapshot(),
      } })
    }
  }
  function stopStreaming(): void {
    const id = activeConversationId.value
    const instance = chat.value
    const messageId = latestMessageId.value ?? instance?.messages.slice().reverse().find(message => message.role === 'user')?.id
    if (!id || !instance || !messageId || stopping.value) return
    stopping.value = true
    runActive.value = true
    localError.value = undefined
    receivedNotice.value = '正在停止…'
    disconnectEvents()
    generation++
    stopRequestVersion++
    // 后端取消独立于浏览器断流；即使发送请求尚未到达，也按消息 ID 阻止启动。
    const cancellation = cancelChatRun(id, messageId)
    void instance.stop()
    void cancellation.then(() => {
      if (chat.value === instance) void resyncFromServer(id)
    }).catch(cause => {
      if (chat.value !== instance) return
      stopping.value = false
      localError.value = cause
      receivedNotice.value = '停止请求失败，请重试'
    })
  }
  async function respondToApproval(id: string, approved: boolean, reason?: string): Promise<void> {
    await chat.value?.addToolApprovalResponse({ id, approved, reason })
    await chat.value?.sendMessage()
  }
  function openConversation(detail: AiWorkUiMessagesResponse): void {
    rememberConversation(detail.conversation_id)
    localTurnStartedAt.value = ''
    const id = detail.conversation_id
    // 页面已校验响应并确认仍选中该会话；打开只绑定历史，不提交模型请求。
    generation++
    disconnectEvents()
    stopRequestVersion++
    activeConversationId.value = id
    chat.value = createChat(id, detail.messages)
    historyVersion.value = detail.history_version
    runActive.value = detail.run_active ?? false
    stopping.value = Boolean(runActive.value && detail.run_status === 'cancelled')
    latestMessageId.value = detail.latest_message_id ?? null
    pendingToolCalls.value = detail.pending_tool_calls ?? []
    localError.value = detail.run_error ? Object.assign(new Error(detail.run_error.message), { code: detail.run_error.code }) : undefined
    targetDraftIds.value = []
    receivedMessages.value = []
    receivedNotice.value = detail.received_messages?.length ? '已收到，等待当前操作结束后应用' : ''
    finishRestore()
    connectEvents(id)
    if (runActive.value) retryTimer = setTimeout(() => void resyncFromServer(id), 200)
  }
  function prepareDrafts(ids: string[], goal = '准备所选草稿，复用已有资料，完成可执行的准备工作并按草稿汇报剩余问题。'): void {
    startConversation()
    targetDraftIds.value = [...new Set(ids)]
    input.value = `${goal}\n目标 draft_id：${targetDraftIds.value.join('、')}。本次仅准备草稿。`
    floatingOpen.value = true
    sendMessage()
  }
  async function forkConversation(messageId: string): Promise<void> {
    const id = activeConversationId.value
    const instance = chat.value
    if (!id || canStop.value || forking.value) return
    forking.value = true
    localError.value = undefined
    try {
      const detail = await forkChatConversation(id, messageId)
      if (chat.value === instance) {
        openConversation(detail)
      }
    } catch (cause) {
      if (chat.value === instance) localError.value = cause as AiChatError
    } finally { forking.value = false }
  }
  function refreshHistory(): void {
    if (activeConversationId.value) void resyncFromServer(activeConversationId.value)
  }
  function clearError(): void { localError.value = undefined; chat.value?.clearError() }
  return { activeConversationId, conversationLinkId, chat, input, floatingOpen, historyVersion, stopping, canStop, status, messages,
    error, isBusy, isRunning, canSend, pendingToolCalls, receivedNotice, receivedMessages, startConversation,
    restoring, restoreError, restoreConversation,
    sendMessage, stopStreaming, respondToApproval, clearError, openConversation, connectEvents, disconnectEvents,
    prepareDrafts, refreshHistory, forking, forkConversation,
    openFloating: () => { floatingOpen.value = true; void restoreConversation() }, closeFloating: () => { floatingOpen.value = false } }
})
