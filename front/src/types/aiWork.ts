export interface PydanticConversationSummary {
  conversation_id: string
  created_at: string
  updated_at: string
}

export interface PydanticConversationListResponse {
  ok: boolean
  conversations: PydanticConversationSummary[]
}

export interface PydanticConversationDetailResponse {
  ok: boolean
  conversation_id: string
  created_at: string
  updated_at: string
  messages: unknown[]
}
