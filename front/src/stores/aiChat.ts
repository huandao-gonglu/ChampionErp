import { computed, ref, shallowRef } from 'vue'
import { defineStore } from 'pinia'
import { Chat } from '@ai-sdk/vue'
import { DefaultChatTransport } from 'ai'
import type { ChatStatus, UIMessage } from 'ai'
import { AI_CHAT_RUNS_PATH, fetchUiMessages } from '@/api/aiWork'
import { GLOBAL_CHAT_CONVERSATION_PREFIX } from '@/types/aiWork'

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

  const status = computed<ChatStatus>(() => chat.value?.status ?? 'ready')
  const messages = computed<UIMessage[]>(() => chat.value?.messages ?? [])
  const error = computed<AiChatError | undefined>(() => chat.value?.error as AiChatError | undefined)
  const isBusy = computed(() => status.value === 'submitted' || status.value === 'streaming')
  const canSend = computed(() => input.value.trim().length > 0 && !isBusy.value)

  async function recoverFromDuplicateClaim(instance: Chat<UIMessage>): Promise<void> {
    try {
      const detail = await fetchUiMessages(instance.id)
      if (detail.ok && chat.value === instance) {
        instance.messages = detail.messages
        instance.clearError()
      }
    } catch {
      // 收敛失败时保留可解释的 error 状态，供界面展示。
    }
  }

  function createChat(conversationId: string, initialMessages: UIMessage[]): Chat<UIMessage> {
    const instance = new Chat<UIMessage>({
      id: conversationId,
      messages: initialMessages,
      transport: createTransport(),
      onFinish: ({ isAbort, isDisconnect, isError }) => {
        if (!isAbort && !isDisconnect && !isError) {
          historyVersion.value += 1
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
    return conversationId
  }

  function newConversation(): void {
    activeConversationId.value = null
    chat.value = null
    input.value = ''
  }

  function sendMessage(): void {
    const text = input.value.trim()
    if (!text || isBusy.value) return
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
      return true
    }
    reactivating.value = true
    try {
      const detail = await fetchUiMessages(conversationId)
      if (!detail.ok) {
        return false
      }
      activeConversationId.value = conversationId
      chat.value = createChat(conversationId, detail.messages)
      return true
    } catch {
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

  function refreshHistory(): void {
    historyVersion.value += 1
  }

  return {
    activeConversationId,
    chat,
    input,
    floatingOpen,
    historyVersion,
    reactivating,
    status,
    messages,
    error,
    isBusy,
    canSend,
    startConversation,
    newConversation,
    sendMessage,
    stopStreaming,
    clearError,
    reactivateConversation,
    openFloating,
    closeFloating,
    refreshHistory,
  }
})
