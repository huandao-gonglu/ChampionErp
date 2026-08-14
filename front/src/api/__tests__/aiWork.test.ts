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

  it('只导出 conversation list/detail 两个读取函数', () => {
    expect(Object.keys(aiWorkApi).sort()).toEqual([
      'fetchPydanticConversation',
      'fetchPydanticConversations',
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
})
