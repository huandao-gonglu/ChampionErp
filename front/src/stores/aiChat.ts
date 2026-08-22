import { computed, ref, shallowRef } from 'vue'
import { defineStore } from 'pinia'
import { Chat } from '@ai-sdk/vue'
import { DefaultChatTransport } from 'ai'
import type { ChatStatus, UIMessage } from 'ai'
import {
  AI_CHAT_RUNS_PATH,
  conversationEventsUrl,
  fetchConversationTaskLink,
  fetchUiMessages,
} from '@/api/aiWork'
import { GLOBAL_CHAT_CONVERSATION_PREFIX } from '@/types/aiWork'
import type { ConversationTaskLinkResponse } from '@/types/aiWork'

/** 后端预流错误码：同一 (conversation, client_message_id) 已被服务端接受。 */
export const AI_CHAT_TURN_ALREADY_ACCEPTED = 'AI_CHAT_TURN_ALREADY_ACCEPTED'

export interface AiChatError extends Error {
  code?: string
  status?: number
}

/** 生成 `conversation_global_chat_<32 位十六进制>` 会话 ID。 */
export function createChatConversationId(): string {
  const random =
    typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
      ? crypto.randomUUID().replace(/-/g, '')
      : Array.from({ length: 32 }, () => Math.floor(Math.random() * 16).toString(16)).join('')
  return `${GLOBAL_CHAT_CONVERSATION_PREFIX}${random}`.slice(
    0,
    GLOBAL_CHAT_CONVERSATION_PREFIX.length + 32,
  )
}

/** 预流错误时解析后端标准 JSON，并把错误码附加到 Error 上；流式响应原样返回。 */
async function chatFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const response = await fetch(input, init)
  if (response.ok) {
    return response
  }
  let code = 'AI_CHAT_HTTP_ERROR'
  let message = `HTTP ${response.status}`
  try {
    const body = (await response.json()) as { error?: string; error_code?: string }
    if (body?.error_code) code = body.error_code
    if (body?.error) message = body.error
  } catch {
    // 非 JSON 错误体保持默认 HTTP 文案。
  }
  const error = new Error(message) as AiChatError
  error.code = code
  error.status = response.status
  throw error
}

function createTransport(): DefaultChatTransport<UIMessage> {
  return new DefaultChatTransport<UIMessage>({
    api: AI_CHAT_RUNS_PATH,
    credentials: 'same-origin',
    fetch: chatFetch,
    // 服务端历史是唯一事实来源：只上传本轮最新一条用户消息，保留 Adapter 需要的 id 与 trigger。
    prepareSendMessagesRequest: ({ id, messages, trigger }) => {
      const latestUserMessage = [...messages].reverse().find((message) => message.role === 'user')
      return {
        body: {
          id,
          trigger,
          messages: latestUserMessage ? [latestUserMessage] : [],
        },
      }
    },
  })
}

export const useAiChatStore = defineStore('aiChat', () => {
  const activeConversationId = ref<string | null>(null)
  const chat = shallowRef<Chat<UIMessage> | null>(null)
  const input = ref('')
  const floatingOpen = ref(false)
  const historyVersion = ref(0)
  const reactivating = ref(false)
  // conversation → 未解决 Deferred 任务的只读关联；由 task-link 纯读接口与
  // 后台官方事件驱动刷新，普通发送在它存在时被锁定（服务端 409 兜底）。
  const taskLink = ref<ConversationTaskLinkResponse | null>(null)

  let eventSource: EventSource | null = null
  let eventsConversationId: string | null = null
  let eventsReconnectTimer: ReturnType<typeof setTimeout> | null = null
  let pendingServerResync = false
  // 报告 R-04/R-06：history 重读与 task-link 重读都可能有多个请求并发在途。
  // generation 单调递增，响应回来时只有最新一次请求的结果允许应用，旧响应
  // 不得覆盖较新的快照（反序完成时尤其重要）。
  let resyncGeneration = 0
  let taskLinkGeneration = 0
  // 报告 A-11：重试状态必须绑定 conversation 与 generation。旧实现用 store 级
  // 计数器且只在成功时归零：会话 A 耗尽重试后切到会话 B，B 的首次失败将永远
  // 得不到重试。现在状态随 conversation 绑定，切换会话（start/new/reactivate/
  // disconnect）清零；新的触发点（批次事件、手动刷新、onFinish）重启周期，
  // 达到短期上限也不是永久放弃。
  let resyncRetryTimer: ReturnType<typeof setTimeout> | null = null
  let resyncRetry = { conversationId: null as string | null, attempts: 0 }
  const RESYNC_MAX_RETRY_ATTEMPTS = 3
  const RESYNC_RETRY_BASE_MS = 500
  // 报告 A-10：task-link 失败也要确定性重新对账。「旧成功 + 新失败」窗口里，
  // 较新请求失败后若不重试，界面会长期停留在陈旧关联（误锁发送或丢失任务卡）。
  let taskLinkRetryTimer: ReturnType<typeof setTimeout> | null = null
  let taskLinkRetry = { conversationId: null as string | null, attempts: 0 }
  const TASK_LINK_MAX_RETRY_ATTEMPTS = 2
  const TASK_LINK_RETRY_BASE_MS = 250

  const status = computed<ChatStatus>(() => chat.value?.status ?? 'ready')
  // AI SDK 的流式增量通过 `replaceMessage` 做数组索引赋值（`messagesRef.value[index] = { ...message }`），
  // 新消息对象复用同一 parts/part 引用，且 SDK 在非响应式对象上原地改写 `part.text`，
  // 直接返回原数组引用无法可靠触发气泡重渲染。这里遍历活动 Chat 的消息建立索引级依赖，
  // 并返回结构化新副本作为渲染桥；唯一事实源仍是 `chat.messages`（及服务端 Pydantic 历史），
  // 该副本仅为渲染派生，不落库、不双写、不当可信历史。
  const messages = computed<UIMessage[]>(() => {
    const current = chat.value?.messages
    if (!current || current.length === 0) return []
    return JSON.parse(JSON.stringify(current)) as UIMessage[]
  })
  const error = computed<AiChatError | undefined>(() => chat.value?.error as AiChatError | undefined)
  const isBusy = computed(() => status.value === 'submitted' || status.value === 'streaming')
  const hasUnresolvedTask = computed(() => Boolean(taskLink.value?.ok && taskLink.value.task_id))
  const sendBlockedReason = computed(() => (
    hasUnresolvedTask.value
      ? '当前会话有进行中的全局任务；请先在任务卡片中审批、补充资料或取消任务，再发送新消息。'
      : ''
  ))
  const canSend = computed(() => (
    input.value.trim().length > 0 && !isBusy.value && !hasUnresolvedTask.value
  ))

  async function recoverFromDuplicateClaim(instance: Chat<UIMessage>): Promise<void> {
    // 报告 A-07：duplicate-claim 恢复与 resyncFromServer 共享同一 generation/
    // version 单调提交规则。旧实现直接赋值 instance.messages、不校验代次也
    // 不更新 historyVersion：当一个 v1 慢请求在途时，continuation 批次已把 v2
    // 提交并推进游标，随后 v1 慢响应会用旧 history 覆盖 v2 消息，而游标仍停在
    // v2，真正的 v2 批次此后被当作重复事件忽略，最终回复从界面消失。
    const conversationId = instance.id
    const generation = ++resyncGeneration
    try {
      const detail = await fetchUiMessages(conversationId)
      // 被更新的写入意图取代、会话已切换、或版本低于已应用版本时丢弃，
      // 陈旧响应绝不能覆盖较新消息。
      if (generation !== resyncGeneration) return
      if (!detail.ok || chat.value !== instance) return
      const incoming = Number(detail.history_version) || 0
      if (incoming < historyVersion.value) return
      instance.messages = detail.messages
      historyVersion.value = incoming
      instance.clearError()
    } catch {
      // 收敛失败时保留可解释的 error 状态，供界面展示。
    }
  }

  /** 只读刷新 conversation → unresolved task 关联；失败时确定性重新对账。 */
  async function refreshTaskLink(): Promise<void> {
    const conversationId = activeConversationId.value
    if (!conversationId) {
      taskLink.value = null
      return
    }
    // 报告 A-10：新的对账意图重启该会话的重试周期。
    taskLinkRetry = { conversationId, attempts: 0 }
    await fetchTaskLinkOnce(conversationId)
  }

  /** 单次 task-link 读取；失败时按退避安排有界重试（报告 A-10）。 */
  async function fetchTaskLinkOnce(conversationId: string): Promise<void> {
    const generation = ++taskLinkGeneration
    try {
      const response = await fetchConversationTaskLink(conversationId)
      // 报告 R-06：同一 conversation 的旧请求可能晚于新请求返回。被更新的
      // 请求取代、或 conversation 已切换时，丢弃结果，旧响应不得覆盖较新的
      // 关联事实（旧 empty 覆盖新 ready 会丢任务卡，反之会误锁发送）。
      if (generation !== taskLinkGeneration) return
      if (activeConversationId.value !== conversationId) return
      taskLink.value = response?.ok ? response : null
      taskLinkRetry = { conversationId, attempts: 0 }
    } catch {
      // 报告 A-10：只读探测失败保留既有 link 状态（避免误放开被锁定的普通
      // 发送），但必须确定性重新对账——否则「旧成功响应被取代 + 新请求失败」
      // 会让界面长期停留在陈旧关联上。
      if (generation !== taskLinkGeneration) return
      if (activeConversationId.value !== conversationId) return
      scheduleTaskLinkRetry(conversationId)
    }
  }

  function clearTaskLinkRetry(): void {
    if (taskLinkRetryTimer !== null) {
      clearTimeout(taskLinkRetryTimer)
      taskLinkRetryTimer = null
    }
  }

  function scheduleTaskLinkRetry(conversationId: string): void {
    if (taskLinkRetry.conversationId !== conversationId) return
    if (taskLinkRetry.attempts >= TASK_LINK_MAX_RETRY_ATTEMPTS) return
    taskLinkRetry.attempts += 1
    const delay = TASK_LINK_RETRY_BASE_MS * 2 ** (taskLinkRetry.attempts - 1)
    clearTaskLinkRetry()
    taskLinkRetryTimer = setTimeout(() => {
      taskLinkRetryTimer = null
      if (activeConversationId.value === conversationId) {
        void fetchTaskLinkOnce(conversationId)
      }
    }, delay)
  }

  /** 报告 A-11：切换会话时清零两类重试状态，旧会话的重试不得泄漏到新会话。 */
  function resetRetryState(): void {
    clearResyncRetry()
    clearTaskLinkRetry()
    resyncRetry = { conversationId: null, attempts: 0 }
    taskLinkRetry = { conversationId: null, attempts: 0 }
  }

  function disconnectEvents(): void {
    if (eventsReconnectTimer !== null) {
      clearTimeout(eventsReconnectTimer)
      eventsReconnectTimer = null
    }
    // 报告 A-10：同一 conversation 的重连（resync reconnect）不得取消刚安排
    // 的 task-link 对账重试。重试状态的清零只发生在会话切换入口
    // （start/new/reactivate 显式调用 resetRetryState），不在这里。
    if (eventSource) {
      eventSource.close()
      eventSource = null
    }
    eventsConversationId = null
  }

  /**
   * 以当前已应用的 history version 为游标建立后台官方事件订阅；
   * 服务端先从 outbox 重放游标之后的保留批次再转 live。
   */
  function connectEvents(conversationId: string): void {
    if (typeof EventSource === 'undefined') return
    if (eventSource && eventsConversationId === conversationId) return
    disconnectEvents()
    eventsConversationId = conversationId
    const source = new EventSource(conversationEventsUrl(conversationId, historyVersion.value))
    source.onmessage = (event: MessageEvent) => {
      handleEventPayload(conversationId, String(event.data ?? ''))
    }
    source.onerror = () => {
      if (eventSource !== source || eventsConversationId !== conversationId) return
      if (source.readyState === EventSource.CLOSED) {
        // 致命断开：以已应用版本为新游标延迟重建订阅。
        // CONNECTING 表示浏览器自带退避重连，仍沿用原 URL 游标，重放 + 版本去重可覆盖。
        disconnectEvents()
        eventsReconnectTimer = setTimeout(() => {
          eventsReconnectTimer = null
          if (activeConversationId.value === conversationId) connectEvents(conversationId)
        }, 2000)
      }
    }
    eventSource = source
  }

  function handleEventPayload(conversationId: string, rawData: string): void {
    if (eventsConversationId !== conversationId) return
    let payload: { type?: unknown; history_version?: unknown }
    try {
      payload = JSON.parse(rawData) as { type?: unknown; history_version?: unknown }
    } catch {
      return
    }
    if (payload.type === 'resync_required') {
      // 游标早于 outbox 保留窗口：重读 /ui-messages 后以新版本重建订阅。
      void resyncFromServer(conversationId, { reconnect: true })
      return
    }
    if (payload.type === 'batch') {
      const version = Number(payload.history_version ?? 0)
      // 只按单调递增版本应用；重复或旧批次仅去重。
      if (!Number.isFinite(version) || version <= historyVersion.value) return
      void resyncFromServer(conversationId, { reconnect: false })
    }
  }

  function clearResyncRetry(): void {
    if (resyncRetryTimer !== null) {
      clearTimeout(resyncRetryTimer)
      resyncRetryTimer = null
    }
  }

  /** 报告 A-11：重读失败后按指数退避重试，直到成功或达到短期上限。 */
  function scheduleResyncRetry(
    conversationId: string,
    options: { reconnect: boolean },
  ): void {
    if (resyncRetry.conversationId !== conversationId) return
    if (resyncRetry.attempts >= RESYNC_MAX_RETRY_ATTEMPTS) return
    resyncRetry.attempts += 1
    const delay = RESYNC_RETRY_BASE_MS * 2 ** (resyncRetry.attempts - 1)
    clearResyncRetry()
    resyncRetryTimer = setTimeout(() => {
      resyncRetryTimer = null
      if (activeConversationId.value === conversationId) {
        void resyncFromServer(conversationId, options, { scheduled: true })
      }
    }, delay)
  }

  /** 以服务端已提交历史为最终事实源重读消息，并刷新任务关联。 */
  async function resyncFromServer(
    conversationId: string,
    options: { reconnect: boolean },
    flags: { scheduled?: boolean } = {},
  ): Promise<void> {
    // 新的重读意图取代尚未触发的重试计时。
    clearResyncRetry()
    if (!flags.scheduled) {
      // 报告 A-11：新触发点（批次事件、手动刷新、onFinish）重启重试周期——
      // 达到短期上限后仍有可恢复触发点，而不是永久放弃。
      resyncRetry = { conversationId, attempts: 0 }
    } else if (resyncRetry.conversationId !== conversationId) {
      resyncRetry = { conversationId, attempts: 0 }
    }
    if (isBusy.value && chat.value?.id === conversationId) {
      // 报告 A-11：流式期间 SDK 官方消息是渲染权威；等本回合 onFinish 后再
      // 应用服务端历史。此处不能用旧游标立即重建订阅——服务端会持续返回同一
      // resync_required，形成重连忙循环；onFinish 收尾时会重读快照并以新
      // 游标重建订阅。
      pendingServerResync = true
      return
    }
    const generation = ++resyncGeneration
    try {
      const detail = await fetchUiMessages(conversationId)
      // 报告 R-04：并发 resync 的响应可能反序完成。只有最新一次请求的响应
      // 允许应用；版本低于已应用版本的响应是陈旧快照，绝不能覆盖较新消息
      // （否则 v3 游标 + v2 消息，且 v3 批次会被当作重复事件忽略）。
      if (generation !== resyncGeneration) return
      if (!detail.ok || chat.value?.id !== conversationId) return
      const incoming = Number(detail.history_version) || 0
      if (incoming < historyVersion.value) return
      chat.value.messages = detail.messages
      historyVersion.value = incoming
      resyncRetry = { conversationId, attempts: 0 }
    } catch {
      // 报告 A-11：重读失败不能只保留旧消息等待手动刷新——已投递批次不会
      // 保证再次出现。按退避确定性重试，确保已提交历史最终可读。
      if (generation !== resyncGeneration) return
      if (activeConversationId.value !== conversationId) return
      scheduleResyncRetry(conversationId, options)
      return
    }
    await refreshTaskLink()
    if (options.reconnect) {
      disconnectEvents()
      connectEvents(conversationId)
    }
  }

  function createChat(conversationId: string, initialMessages: UIMessage[]): Chat<UIMessage> {
    const instance = new Chat<UIMessage>({
      id: conversationId,
      messages: initialMessages,
      transport: createTransport(),
      onFinish: ({ isAbort, isDisconnect, isError }) => {
        // history version 不做本地猜测：本回合结束（或流式期间积压了官方
        // 事件）时重读服务端已提交历史，用服务端版本对齐订阅游标。
        const cleanFinish = !isAbort && !isDisconnect && !isError
        const hadPendingResync = pendingServerResync
        if (cleanFinish || hadPendingResync) {
          pendingServerResync = false
          // 报告 A-11：busy 期间积压了 resync_required 时，本回合结束后不仅
          // 重读快照，还要以新游标重建订阅（busy 分支不再立即重连，避免旧
          // 游标重连忙循环）。
          void resyncFromServer(instance.id, { reconnect: hadPendingResync })
        }
      },
      onError: (chatError) => {
        const code = (chatError as AiChatError).code
        if (code === AI_CHAT_TURN_ALREADY_ACCEPTED) {
          void recoverFromDuplicateClaim(instance)
        }
      },
    })
    return instance
  }

  function startConversation(): string {
    const conversationId = createChatConversationId()
    activeConversationId.value = conversationId
    chat.value = createChat(conversationId, [])
    historyVersion.value = 0
    taskLink.value = null
    pendingServerResync = false
    // 使上一会话仍在途的重读/关联响应作废，并清零其重试状态（报告 A-11）。
    resyncGeneration += 1
    taskLinkGeneration += 1
    resetRetryState()
    connectEvents(conversationId)
    return conversationId
  }

  function newConversation(): void {
    disconnectEvents()
    activeConversationId.value = null
    chat.value = null
    input.value = ''
    taskLink.value = null
    pendingServerResync = false
    resyncGeneration += 1
    taskLinkGeneration += 1
    resetRetryState()
  }

  function sendMessage(): void {
    const text = input.value.trim()
    if (!text || isBusy.value) return
    if (text === '/new') {
      input.value = ''
      startConversation()
      return
    }
    // 未解决 Deferred 任务存在时锁定普通发送；/new 与审批/输入/取消不受影响。
    if (hasUnresolvedTask.value) return
    if (!chat.value) {
      startConversation()
    }
    input.value = ''
    void chat.value?.sendMessage({ text })
  }

  function stopStreaming(): void {
    chat.value?.stop()
  }

  function clearError(): void {
    chat.value?.clearError()
  }

  /** 重新激活一个已完成的 global.chat 历史：用服务端派生消息初始化新的活动 Chat。 */
  async function reactivateConversation(conversationId: string): Promise<boolean> {
    if (chat.value?.id === conversationId) {
      activeConversationId.value = conversationId
      connectEvents(conversationId)
      void refreshTaskLink()
      return true
    }
    reactivating.value = true
    disconnectEvents()
    try {
      const detail = await fetchUiMessages(conversationId)
      if (!detail.ok) {
        return false
      }
      activeConversationId.value = conversationId
      chat.value = createChat(conversationId, detail.messages)
      // 冷启动：先采用服务端历史版本作为订阅游标，再建立重放后无缝转 live 的订阅。
      historyVersion.value = Number(detail.history_version) || 0
      taskLink.value = null
      pendingServerResync = false
      // 使切换前会话仍在途的重读/关联响应作废，防止旧响应覆盖冷启动快照；
      // 同时清零切换前会话的重试状态（报告 A-11）。
      resyncGeneration += 1
      taskLinkGeneration += 1
      resetRetryState()
      connectEvents(conversationId)
      void refreshTaskLink()
      return true
    } catch {
      // 重激活失败：恢复原活动 conversation 的订阅，避免静默失去事件通道。
      if (activeConversationId.value) connectEvents(activeConversationId.value)
      return false
    } finally {
      reactivating.value = false
    }
  }

  function openFloating(): void {
    floatingOpen.value = true
  }

  function closeFloating(): void {
    floatingOpen.value = false
  }

  /** 以服务端已提交历史刷新游标；不做本地版本猜测。 */
  function refreshHistory(): void {
    const conversationId = activeConversationId.value
    if (!conversationId || chat.value?.id !== conversationId) return
    void resyncFromServer(conversationId, { reconnect: false })
  }

  return {
    activeConversationId,
    chat,
    input,
    floatingOpen,
    historyVersion,
    reactivating,
    taskLink,
    status,
    messages,
    error,
    isBusy,
    canSend,
    hasUnresolvedTask,
    sendBlockedReason,
    startConversation,
    newConversation,
    sendMessage,
    stopStreaming,
    clearError,
    reactivateConversation,
    refreshTaskLink,
    connectEvents,
    disconnectEvents,
    openFloating,
    closeFloating,
    refreshHistory,
  }
})
