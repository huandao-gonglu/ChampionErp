import { apiClient } from './client'
import type {
  AiWorkConversationListResponse,
  AiWorkConversationResponse,
  AiWorkEvent,
} from '@/types/aiWork'

const CONVERSATIONS_PATH = '/api/v1/ai-work/conversations'

export async function fetchAiWorkConversations(limit = 50): Promise<AiWorkConversationListResponse> {
  const response = await apiClient.get<AiWorkConversationListResponse>(CONVERSATIONS_PATH, {
    params: { limit },
  })
  return response.data
}

export async function fetchAiWorkConversation(conversationId: string): Promise<AiWorkConversationResponse> {
  const response = await apiClient.get<AiWorkConversationResponse>(
    `${CONVERSATIONS_PATH}/${encodeURIComponent(conversationId)}`,
  )
  return response.data
}

export async function waitForAiWorkEvents(
  conversationId: string,
  afterSeq: number,
  waitMs = 20_000,
): Promise<AiWorkEvent[]> {
  const response = await apiClient.get<string>(
    `${CONVERSATIONS_PATH}/${encodeURIComponent(conversationId)}/events`,
    {
      params: {
        after_seq: afterSeq,
        wait_ms: waitMs,
      },
      responseType: 'text',
      transformResponse: [(value) => value],
      timeout: waitMs + 10_000,
    },
  )
  const text = typeof response.data === 'string' ? response.data : String(response.data || '')
  return text
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => JSON.parse(line) as AiWorkEvent)
}

export function aiWorkRawUrl(conversationId: string): string {
  return `${CONVERSATIONS_PATH}/${encodeURIComponent(conversationId)}/raw`
}

