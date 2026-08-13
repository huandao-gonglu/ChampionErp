import { beforeEach, describe, expect, it, vi } from 'vitest'
import { apiClient } from '@/api/client'
import {
  fetchAiWorkConversation,
  fetchAiWorkConversationChildren,
  fetchAiWorkConversations,
  waitForAiWorkEvents,
} from '@/api/aiWork'

vi.mock('@/api/client', () => ({
  apiClient: {
    get: vi.fn(),
  },
}))

describe('AI Work 会话层级 API', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(apiClient.get).mockResolvedValue({ data: { ok: true, conversations: [] } })
  })

  it('默认只请求根会话，显式开关才包含内部会话', async () => {
    await fetchAiWorkConversations()
    await fetchAiWorkConversations(80, true)

    expect(vi.mocked(apiClient.get).mock.calls).toEqual([
      ['/api/v1/ai-work/conversations', { params: { limit: 50 } }],
      ['/api/v1/ai-work/conversations', {
        params: { limit: 80, include_children: true },
      }],
    ])
  })

  it('主会话的子执行列表和单会话详情使用独立只读端点', async () => {
    await fetchAiWorkConversationChildren('root/id', 200)
    await fetchAiWorkConversation('child/id')

    expect(vi.mocked(apiClient.get).mock.calls).toEqual([
      ['/api/v1/ai-work/conversations/root%2Fid/children', { params: { limit: 200 } }],
      ['/api/v1/ai-work/conversations/child%2Fid'],
    ])
  })

  it('长轮询透传 AbortSignal 并解析 NDJSON 事件', async () => {
    const abortController = new AbortController()
    vi.mocked(apiClient.get).mockResolvedValue({
      data: '{"seq":8,"type":"RUN_STARTED"}\n{"seq":9,"type":"RUN_FINISHED"}\n',
    })

    const events = await waitForAiWorkEvents(
      'conversation/id',
      7,
      5_000,
      abortController.signal,
    )

    expect(vi.mocked(apiClient.get)).toHaveBeenCalledWith(
      '/api/v1/ai-work/conversations/conversation%2Fid/events',
      expect.objectContaining({
        params: { after_seq: 7, wait_ms: 5_000 },
        signal: abortController.signal,
        timeout: 15_000,
      }),
    )
    expect(events).toEqual([
      { seq: 8, type: 'RUN_STARTED' },
      { seq: 9, type: 'RUN_FINISHED' },
    ])
  })
})
