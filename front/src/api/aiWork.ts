import { apiClient } from './client'
import type {
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
