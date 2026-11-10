import { apiClient } from './client'
import type {
  AiWorkUiMessagesResponse,
  AiChatCancelResponse,
  PydanticConversationDetailResponse,
  PydanticConversationListResponse,
} from '@/types/aiWork'

const CONVERSATIONS_PATH = '/api/v1/ai-work/conversations'

export async function fetchPydanticConversations(
  limit = 100, scope: 'all' | 'chats' = 'all', offset = 0, signal?: AbortSignal,
): Promise<PydanticConversationListResponse> {
  const response = await apiClient.get<PydanticConversationListResponse>(CONVERSATIONS_PATH, {
    params: { limit, scope, offset }, signal,
    ...(scope === 'chats' ? { timeout: 15_000 } : {}),
  })
  return response.data
}

export type ChatManagementAction = 'pin' | 'unpin' | 'rename' | 'delete'

export async function manageConversation(id: string, action: ChatManagementAction, title?: string) {
  await apiClient.post('/api/v1/ai-chat/manage', { id, action, title })
}

export async function fetchConversationTitle(id: string) {
  const response = await apiClient.get<{ title: string; title_pending: boolean; title_manual?: boolean }>(
    `${CONVERSATIONS_PATH}/${encodeURIComponent(id)}/title`,
  )
  return response.data
}

export async function fetchPydanticConversation(
  conversationId: string,
): Promise<PydanticConversationDetailResponse> {
  const response = await apiClient.get<PydanticConversationDetailResponse>(
    `${CONVERSATIONS_PATH}/${encodeURIComponent(conversationId)}`,
  )
  return response.data
}

export async function fetchUiMessages(
  conversationId: string, signal?: AbortSignal,
): Promise<AiWorkUiMessagesResponse> {
  const response = await apiClient.get<AiWorkUiMessagesResponse>(
    `${CONVERSATIONS_PATH}/${encodeURIComponent(conversationId)}/ui-messages`, { signal },
  )
  return response.data
}

/** 后台官方事件订阅 SSE URL；断线重连时携带已应用的 history version。 */
export function conversationEventsUrl(
  conversationId: string,
  afterHistoryVersion: number,
): string {
  const encoded = encodeURIComponent(conversationId)
  return (
    `${CONVERSATIONS_PATH}/${encoded}/events`
    + `?after_history_version=${Math.max(0, Math.floor(afterHistoryVersion))}`
  )
}

/** 聊天流接口不走 Axios；此路径常量供共享 Chat transport 使用。 */
export const AI_CHAT_RUNS_PATH = '/api/v1/ai-chat/runs'

export async function forkChatConversation(conversationId: string, messageId: string): Promise<AiWorkUiMessagesResponse> {
  const response = await apiClient.post<AiWorkUiMessagesResponse>('/api/v1/ai-chat/fork', {
    id: conversationId, message_id: messageId,
  })
  return response.data
}

export async function updateQueuedMessage(id: string, messageId: string, action: 'delete' | 'send'): Promise<void> {
  await apiClient.post('/api/v1/ai-chat/queue', { id, message_id: messageId, action })
}

export async function cancelChatRun(conversationId: string, messageId: string): Promise<AiChatCancelResponse> {
  const response = await apiClient.post<AiChatCancelResponse>('/api/v1/ai-chat/cancel', {
    id: conversationId, message_id: messageId,
  })
  return response.data
}
