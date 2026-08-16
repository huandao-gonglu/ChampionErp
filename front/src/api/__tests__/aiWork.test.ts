import { beforeEach, describe, expect, it, vi } from 'vitest'
import { apiClient } from '@/api/client'
import * as aiWorkApi from '@/api/aiWork'

vi.mock('@/api/client', () => ({
  apiClient: {
    get: vi.fn(),
  },
}))

describe('AI Work 只读 API', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(apiClient.get).mockResolvedValue({ data: { ok: true, conversations: [] } })
  })

  it('只导出规范读取、派生读取与流路径常量', () => {
    expect(Object.keys(aiWorkApi).sort()).toEqual([
      'AI_CHAT_RUNS_PATH',
      'fetchPydanticConversation',
      'fetchPydanticConversations',
      'fetchUiMessages',
    ])
  })

  it('读取 conversation 索引并使用显式 limit', async () => {
    await aiWorkApi.fetchPydanticConversations()
    await aiWorkApi.fetchPydanticConversations(25)

    expect(vi.mocked(apiClient.get).mock.calls).toEqual([
      ['/api/v1/ai-work/conversations', { params: { limit: 100 } }],
      ['/api/v1/ai-work/conversations', { params: { limit: 25 } }],
    ])
  })

  it('详情只请求 conversation 本身并编码 ID', async () => {
    await aiWorkApi.fetchPydanticConversation('conversation/id')

    expect(apiClient.get).toHaveBeenCalledWith(
      '/api/v1/ai-work/conversations/conversation%2Fid',
    )
  })

  it('派生消息读取 /ui-messages 子路径并编码 ID', async () => {
    await aiWorkApi.fetchUiMessages('conversation_global_chat_abc')

    expect(apiClient.get).toHaveBeenCalledWith(
      '/api/v1/ai-work/conversations/conversation_global_chat_abc/ui-messages',
    )
  })

  it('聊天流路径常量固定且不走 Axios', () => {
    expect(aiWorkApi.AI_CHAT_RUNS_PATH).toBe('/api/v1/ai-chat/runs')
  })
})
