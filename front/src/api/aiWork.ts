import { apiClient } from './client'
import type {
  AiWorkUiMessagesResponse,
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

/** 聊天流接口不走 Axios；此路径常量供共享 Chat transport 使用。 */
export const AI_CHAT_RUNS_PATH = '/api/v1/ai-chat/runs'
