import { apiClient } from './client'
import type {
  AiWorkUiMessagesResponse,
  ConversationTaskLinkResponse,
  PydanticConversationDetailResponse,
  PydanticConversationListResponse,
} from '@/types/aiWork'

const CONVERSATIONS_PATH = '/api/v1/ai-work/conversations'

export async function fetchPydanticConversations(
  limit = 100,
): Promise<PydanticConversationListResponse> {
  const response = await apiClient.get<PydanticConversationListResponse>(CONVERSATIONS_PATH, {
    params: { limit },
  })
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
  conversationId: string,
): Promise<AiWorkUiMessagesResponse> {
  const response = await apiClient.get<AiWorkUiMessagesResponse>(
    `${CONVERSATIONS_PATH}/${encodeURIComponent(conversationId)}/ui-messages`,
  )
  return response.data
}

/** conversation → 未解决 Deferred 任务的只读关联（仅返回 ready link）。 */
export async function fetchConversationTaskLink(
  conversationId: string,
): Promise<ConversationTaskLinkResponse> {
  const response = await apiClient.get<ConversationTaskLinkResponse>(
    `${CONVERSATIONS_PATH}/${encodeURIComponent(conversationId)}/task-link`,
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
