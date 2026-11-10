import { computed, ref, shallowRef, nextTick, watch } from 'vue'
import { defineStore } from 'pinia'
import { Chat } from '@ai-sdk/vue'
import { DefaultChatTransport } from 'ai'
import { accountFetch, businessStorageKey } from '@/utils/accountTransport'
import { useChatImages } from '@/composables/useChatImages'
import type { FileUIPart, UIMessage } from 'ai'
import { AI_CHAT_RUNS_PATH, cancelChatRun, updateQueuedMessage, conversationEventsUrl, fetchConversationTitle, fetchUiMessages, forkChatConversation } from '@/api/aiWork'
import { apiClient } from '@/api/client'
import { matchChatCommand } from '@/services/chatCommands'
import type { ChatCommandContext } from '@/services/chatCommands'
import { GLOBAL_CHAT_CONVERSATION_PREFIX } from '@/types/aiWork'
import type { AiWorkUiMessagesResponse, PendingToolCall } from '@/types/aiWork'
import { useAiPageContextStore } from './aiPageContext'
import { projectChatMessages } from '@/utils/aiChatMessages'
import { handoffModelOperation, type DesktopChatState } from '@/utils/desktopHandoff'

export const AI_CHAT_TURN_ALREADY_ACCEPTED = 'AI_CHAT_TURN_ALREADY_ACCEPTED'
export const AI_CHAT_SELECTION_KEY = 'ai-chat-selected-conversation'
export interface AiChatError extends Error { code?: string; status?: number }
export function createChatConversationId(): string {
  return GLOBAL_CHAT_CONVERSATION_PREFIX + crypto.randomUUID().replace(/-/g, '')
}

export const useAiChatStore = defineStore('aiChat', () => {
  const selectionKey = businessStorageKey(AI_CHAT_SELECTION_KEY)
  const pageContext = useAiPageContextStore()
  const activeConversationId = ref<string | null>(null)
  const chat = shallowRef<Chat<UIMessage> | null>(null)
  const input = ref('')
  const images = useChatImages()
  const resumeOpen = ref(false)
  function openResume() { resumeOpen.value = true }
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
  const parallelReplies = ref<AiWorkUiMessagesResponse[]>([])
  const queueAction = ref('')
  const parallelActive = computed(() => parallelReplies.value.some(reply => reply.run_active || reply.received_messages?.length || reply.pending_tool_calls?.length))
  const queueRows = computed(() => [
    ...receivedMessages.value.map(row => ({ ...row, conversation_id: activeConversationId.value! })),
    ...parallelReplies.value.flatMap(reply => (reply.received_messages ?? []).map(row => ({ ...row, conversation_id: reply.conversation_id }))),
  ])
  let directRunMessageId: string | null = null
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
  const messages = computed<UIMessage[]>(previous => projectChatMessages(
    chat.value?.messages ?? [], localTurnStartedAt.value, previous,
  ))
  const firstLine = computed(() => {
    const opening = chat.value?.messages.find(message => message.role === 'user')
    const text = opening?.parts.filter(part => part.type === 'text').map(part => part.text).join(' ').trim().split(/[\r\n]|(?<=[。！？!?])/, 1)[0].replace(/\s+/g, ' ')
    const chars = Array.from(text || '')
    if (!chars.length) return opening ? '图片对话' : ''
    return chars.slice(0, 24).join('') + (chars.length > 24 ? '…' : '')
  })
  const conversationListVersion = ref(0)
  function applyConversationChange(id: string, action: string, title?: string) {
    if (id === activeConversationId.value) {
      if (action === 'rename' && title) savedTitle.value = { id, title }
      if (action === 'delete') {
        input.value = ''
        startConversation()
      }
    }
    conversationListVersion.value++
  }
  const savedTitle = ref<{ id: string; title: string }>()
  const activeTitle = computed(() => (savedTitle.value?.id === activeConversationId.value && savedTitle.value?.title)
    || firstLine.value || '新对话')
  watch([activeConversationId, firstLine, historyVersion, conversationListVersion], ([id, opening, version], _, cleanup) => {
    if (!id || (!opening && !version)) return
    let active = true
    let timer: ReturnType<typeof setTimeout>
    const until = Date.now() + 120_000
    cleanup(() => { active = false; clearTimeout(timer) })
    async function readTitle(key: string) {
      let retry = false
      try {
        const result = await fetchConversationTitle(key)
        if (!active) return
        if (result.title && (result.title_manual || !(opening && result.title === '新对话'))) savedTitle.value = { id: key, title: result.title }
        retry = result.title_pending || !result.title || (!version && result.title === '新对话')
      } catch { retry = true }
      if (active && retry && Date.now() < until) timer = setTimeout(() => void readTitle(key), 2500)
    }
    void readTitle(id)
  })
  const error = computed(() => localError.value ?? chat.value?.error as AiChatError | undefined)
  const canSend = computed(() => Boolean(input.value.trim() || images.attachments.value.length) && !images.reading.value
    && !(images.attachments.value.length && images.blocked.value) && !stopping.value && !forking.value && !restoring.value && !restoreError.value)
  const canStop = computed(() => isBusy.value || runActive.value || pendingToolCalls.value.length > 0 || stopping.value)

  // 只记住选择，不复制消息；消息与活动运行始终从服务端恢复。
  function rememberConversation(id: string | null): void {
    try {
      if (id) localStorage.setItem(selectionKey, id)
      else localStorage.removeItem(selectionKey)
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
    try { id = localStorage.getItem(selectionKey) } catch { /* 无可恢复选择。 */ }
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
        if ((cause as AiChatError)?.code === 'CHAT_DELETED') {
          rememberConversation(null)
          finishRestore()
          return
        }
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
  function connectEvents(conversationId: string, afterVersion = historyVersion.value): void {
    if (activeConversationId.value !== conversationId) return
    disconnectEvents()
    if (typeof EventSource === 'undefined') return
    const source = new EventSource(conversationEventsUrl(conversationId, afterVersion))
    eventSource = source
    source.onmessage = () => {
      if (eventSource !== source) return
      source.close()
      void resyncFromServer(conversationId)
    }
    source.onerror = () => {
      source.close()
      if (eventSource !== source) return
      retryTimer = setTimeout(() => void resyncFromServer(conversationId), 1000)
    }
  }
  async function resyncFromServer(conversationId: string, attempt = 0, force = false): Promise<void> {
    if (activeConversationId.value !== conversationId) return
    if (!force && !parallelActive.value && isBusy.value && (!receivedMessages.value.length || stopping.value)) {
      if (stopping.value) retryTimer = setTimeout(() => void resyncFromServer(conversationId), 500)
      return
    }
    const requestGeneration = ++generation
    try {
      const detail = await fetchUiMessages(conversationId)
      if (requestGeneration !== generation || chat.value?.id !== conversationId) return
      if (detail.history_version < historyVersion.value) return
      // 原生流持有正在输出的消息；此时只同步等待列表，不用历史快照覆盖实时正文。
      receivedMessages.value = (detail.received_messages ?? []).filter(row => row.message_id !== directRunMessageId)
      pendingToolCalls.value = detail.pending_tool_calls ?? []
      parallelReplies.value = detail.parallel_replies ?? []
      if (!pendingInputIds.size) latestMessageId.value = detail.latest_message_id ?? latestMessageId.value
      if (parallelActive.value) {
        if (retryTimer) clearTimeout(retryTimer)
        retryTimer = setTimeout(() => void resyncFromServer(conversationId, 0, true), 1000)
      }
      if (isBusy.value) {
        if (receivedMessages.value.length && !parallelActive.value) {
          connectEvents(conversationId, detail.history_version)
          if (typeof EventSource === 'undefined') retryTimer = setTimeout(() => void resyncFromServer(conversationId), 2000)
        }
        return
      }
      runActive.value = detail.run_active ?? false
      if (runActive.value && detail.run_status === 'cancelled') stopping.value = true
      if (stopping.value && runActive.value) {
        retryTimer = setTimeout(() => void resyncFromServer(conversationId), 2000)
        return
      }
      stopping.value = false
      chat.value.messages = detail.messages
      historyVersion.value = detail.history_version
      localError.value = detail.run_error ? Object.assign(new Error(detail.run_error.message), { code: detail.run_error.code }) : undefined
      receivedNotice.value = ''
      if (!parallelActive.value) connectEvents(conversationId)
      if (!parallelActive.value && (pendingToolCalls.value.length || (runActive.value && typeof EventSource === 'undefined'))) retryTimer = setTimeout(() => void resyncFromServer(conversationId), 2000)
    } catch (cause) {
      if (requestGeneration !== generation || activeConversationId.value !== conversationId) return
      if ((cause as AiChatError)?.code === 'CHAT_DELETED') {
        applyConversationChange(conversationId, 'delete')
        return
      }
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
    const response = await accountFetch(url, init)
    if (response.status === 202) {
      const receipt = await response.json()
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
          if (latest?.role === 'user' && activeConversationId.value === id) latestMessageId.value = latest.id
          return { body: { id, trigger, queue_if_busy: true, messages: latest ? [latest] : [], ...body } }
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
    images.clear()
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
    parallelReplies.value = []
    queueAction.value = ''
    receivedNotice.value = ''
    targetDraftIds.value = []
    localError.value = undefined
    stopping.value = false
    runActive.value = false
    latestMessageId.value = null
    directRunMessageId = null
    chat.value = createChat(id, [])
    connectEvents(id)
    return id
  }
  async function queueMessage(text: string, files: FileUIPart[], targetId?: string): Promise<void> {
    const currentId = activeConversationId.value
    const id = targetId ?? currentId
    if (!id) return
    const messageId = crypto.randomUUID()
    if (id === currentId) latestMessageId.value = messageId
    const requestVersion = stopRequestVersion
    pendingInputIds.add(messageId)
    let response: Response
    try {
      response = await accountFetch(AI_CHAT_RUNS_PATH, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({
        id, queue_if_busy: true, trigger: 'submit-message', messages: [{ id: messageId, role: 'user', parts: [{ type: 'text', text }, ...files] }],
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
    if (!currentId || activeConversationId.value !== currentId) { await response.body?.cancel(); return }
    if (response.status === 202 && id === currentId) {
      if (!receivedMessages.value.some(row => row.message_id === messageId)) {
        receivedMessages.value.push({ message_id: messageId, text })
      }
    } else if (id === currentId) {
      directRunMessageId = messageId
    }
    await response.body?.cancel()
    if (requestVersion === stopRequestVersion) await resyncFromServer(currentId, 0, true)
  }
  function commandContext(): ChatCommandContext {
    return { isBusy: canStop.value, openResume, startConversation, stopStreaming, refreshHistory }
  }
  function sendMessage(): void {
    if (stopping.value || forking.value) return
    const command = !images.attachments.value.length && matchChatCommand(input.value)
    if (command && command.command.available(commandContext())) {
      if (command.command.execute(commandContext(), command.arg)) input.value = ''
      return
    }
    if (!restored && !chat.value) void restoreConversation()
    if (restoring.value || restoreError.value || !canSend.value) return
    const attachments = images.attachments.value
    const files = attachments.map(item => item.part)
    const text = input.value.trim() || '请读取图片上的内容。'
    if (handoffModelOperation('global.chat', {
      target: floatingOpen.value ? undefined : '/aiWork' + (conversationLinkId.value ? `?conversation_id=${encodeURIComponent(conversationLinkId.value)}` : ''),
      state: { chat: { text: input.value, attachments, conversationId: conversationLinkId.value, floating: floatingOpen.value, draftIds: targetDraftIds.value } },
    })) return
    if (!chat.value) startConversation()
    rememberConversation(activeConversationId.value)
    images.clear()
    input.value = ''
    localError.value = undefined
    const instance = chat.value
    function restoreInput() {
      if (chat.value === instance && !input.value && !images.attachments.value.length && !images.reading.value) {
        input.value = text
        images.attachments.value = attachments
      }
    }
    if (isBusy.value || runActive.value || pendingToolCalls.value.length || parallelActive.value) {
      const activeReply = !isBusy.value && !runActive.value && !pendingToolCalls.value.length
        ? parallelReplies.value.slice().reverse().find(reply => reply.run_active || reply.pending_tool_calls?.length)
        : undefined
      void queueMessage(text, files, activeReply?.conversation_id).catch(cause => {
        if (chat.value !== instance) return
        localError.value = cause
        restoreInput()
      })
    } else {
      // 作废发送前发起的历史读取，防止旧快照覆盖新一轮回复或误报排队。
      generation++
      disconnectEvents()
      localTurnStartedAt.value = new Date().toISOString()
      void instance?.sendMessage(files.length ? { text, files } : { text }, { body: {
        target_draft_ids: [...targetDraftIds.value],
        page_context: pageContext.snapshot(),
      } }).then(() => {
        const status = (instance.error as AiChatError | undefined)?.status
        if (files.length && status && status >= 400 && status < 500) restoreInput()
      })
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
  async function submitPending(scope: string, message: UIMessage, answerTo?: string, approvalToken?: string) {
    const root = activeConversationId.value
    if (!root) throw new Error('当前对话已关闭')
    try {
      const response = await accountFetch(AI_CHAT_RUNS_PATH, {
        method: 'POST', headers: { 'Content-Type': 'application/json', ...(approvalToken && { 'X-Approval-Token': approvalToken }) },
        body: JSON.stringify({ id: scope, answer_to: answerTo, trigger: 'submit-message', messages: [message] }),
      })
      if (!response.ok) throw new Error((await response.json()).error ?? '提交失败，请重试')
      await response.body?.cancel()
    } finally {
      await resyncFromServer(root, 0, true)
    }
  }
  function answerQuestion(scope: string, callId: string, text: string) {
    return submitPending(scope, { id: crypto.randomUUID(), role: 'user', parts: [{ type: 'text', text }] }, callId)
  }
  async function submitApproval(scope: string, messages: UIMessage[], id: string, approved: boolean, reason?: string) {
    const root = activeConversationId.value
    for (const message of messages) {
      const part = message.parts.find(part => 'state' in part && part.state === 'approval-requested' && part.approval.id === id)
      if (!part || !('state' in part) || part.state !== 'approval-requested') continue
      // 只交给 SDK 当前一项，其他待办不随本次决定提交。
      const decision = new Chat<UIMessage>({ id: scope, messages: [{ ...message, parts: [part] }] })
      await decision.addToolApprovalResponse({ id, approved, reason })
      const { data } = await apiClient.get<{ approvalToken: string }>('/api/state')
      if (activeConversationId.value !== root) return
      await submitPending(scope, decision.messages[0], undefined, data.approvalToken)
      return
    }
    throw new Error('这项审批已失效，请查看当前待办。')
  }
  function respondToApproval(id: string, approved: boolean, reason?: string) {
    if (!chat.value) throw new Error('当前对话已关闭')
    return submitApproval(chat.value.id, chat.value.messages, id, approved, reason)
  }
  function openConversation(detail: AiWorkUiMessagesResponse): void {
    rememberConversation(detail.conversation_id)
    localTurnStartedAt.value = ''
    images.clear()
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
    directRunMessageId = null
    receivedMessages.value = detail.received_messages ?? []
    queueAction.value = ''
    parallelReplies.value = detail.parallel_replies ?? []
    receivedNotice.value = ''
    finishRestore()
    connectEvents(id)
    if (runActive.value || parallelActive.value || pendingToolCalls.value.length) retryTimer = setTimeout(() => void resyncFromServer(id), 200)
  }
  function estimatePackageDimensions(draftId: string, skuIds: string[], markets: string[]): void {
    startConversation()
    targetDraftIds.value = [draftId]
    input.value = `请估算草稿 ${draftId} 中下列 SKU 缺失的包装外尺寸：${skuIds.join('、')}。按已保存的核价设置比较${markets.join('、')}的物流影响，列出区间和建议值供我确认。`
    floatingOpen.value = true
    sendMessage()
  }
  function restoreDesktopInput(state: DesktopChatState): void {
    input.value = state.text
    images.attachments.value = state.attachments
    targetDraftIds.value = state.draftIds || []
    floatingOpen.value = state.floating === true
  }
  function prepareDrafts(ids: string[], goal = '准备所选草稿，复用已有资料，完成可执行的准备工作并按草稿汇报剩余问题。'): void {
    startConversation()
    targetDraftIds.value = [...new Set(ids)]
    input.value = `${goal}\n目标 draft_id：${targetDraftIds.value.join('、')}。本次仅准备草稿，包含共用主图和 SKU 套图生成；手动修改过的图片先确认保留或重做，原图保留。`
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
  async function actOnQueue(id: string, messageId: string, action: 'send' | 'delete') {
    const currentId = activeConversationId.value
    const instance = chat.value
    if (!currentId || queueAction.value) return
    queueAction.value = messageId
    localError.value = undefined
    try {
      await updateQueuedMessage(id, messageId, action)
      if (chat.value === instance) await resyncFromServer(currentId, 0, true)
    } catch (cause) {
      if (chat.value === instance) {
        await resyncFromServer(currentId, 0, true)
        if (chat.value === instance) localError.value = cause as AiChatError
      }
    } finally {
      if (chat.value === instance) queueAction.value = ''
    }
  }
  async function stopParallelReply(reply: AiWorkUiMessagesResponse) {
    const id = activeConversationId.value
    if (!id || !reply.latest_message_id) return
    try {
      await cancelChatRun(reply.conversation_id, reply.latest_message_id)
      await resyncFromServer(id, 0, true)
    } catch (cause) {
      if (activeConversationId.value === id) localError.value = cause as AiChatError
    }
  }
  async function respondToParallelApproval(reply: AiWorkUiMessagesResponse, id: string, approved: boolean) {
    await submitApproval(reply.conversation_id, reply.messages, id, approved)
  }
  function clearError(): void { localError.value = undefined; chat.value?.clearError() }
  return { images, resumeOpen, openResume, activeTitle, conversationListVersion, applyConversationChange, activeConversationId, conversationLinkId, chat, input, floatingOpen, historyVersion, stopping, canStop, status, messages,
    error, isBusy, isRunning, canSend, pendingToolCalls, receivedNotice, receivedMessages, startConversation,
    restoring, restoreError, restoreConversation, actOnQueue, queueAction, queueRows, parallelReplies, stopParallelReply, respondToParallelApproval,
    sendMessage, stopStreaming, respondToApproval, answerQuestion, clearError, openConversation, connectEvents, disconnectEvents,
    prepareDrafts, estimatePackageDimensions, restoreDesktopInput, refreshHistory, forking, forkConversation,
    openFloating: () => { floatingOpen.value = true; void restoreConversation() }, closeFloating: () => { floatingOpen.value = false } }
})
