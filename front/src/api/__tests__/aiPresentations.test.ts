import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { apiClient } from '@/api/client'
import {
  AI_PRESENTATIONS_PATH,
  createPresentationObserveChat,
  describePresentationStatus,
  fetchAiPresentationStatus,
  normalizePresentationDescriptor,
  presentationStreamPath,
  reserveAiPresentation,
} from '@/api/aiPresentations'

vi.mock('@/api/client', () => ({
  API_REQUEST_TIMEOUT_MS: 30000,
  apiClient: {
    post: vi.fn(),
    get: vi.fn(),
  },
}))

const DESCRIPTOR = {
  presentation_id: 'presentation_a',
  conversation_id: 'conversation_a',
  display_title: 'AI 填充属性',
  status: 'reserved',
}

describe('aiPresentations 通用展示边界', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    setActivePinia(createPinia())
  })

  it('normalizePresentationDescriptor 要求 presentation_id/conversation_id 并归一化状态', () => {
    expect(normalizePresentationDescriptor(DESCRIPTOR)).toEqual({
      presentationId: 'presentation_a',
      conversationId: 'conversation_a',
      displayTitle: 'AI 填充属性',
      status: 'reserved',
    })
    // 未知状态归一化为活动态，不得抛错。
    expect(normalizePresentationDescriptor({
      presentation_id: 'p',
      conversation_id: 'c',
      status: 'weird',
    }).status).toBe('running')
    expect(() => normalizePresentationDescriptor({ conversation_id: 'c' }))
      .toThrow('presentation_id')
    expect(() => normalizePresentationDescriptor({ presentation_id: 'p' }))
      .toThrow('conversation_id')
  })

  it('reserveAiPresentation POST 通用 reserve endpoint，只携带 display_title', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({ data: DESCRIPTOR, status: 200 })

    const descriptor = await reserveAiPresentation('AI 填充属性')

    expect(apiClient.post).toHaveBeenCalledWith(
      AI_PRESENTATIONS_PATH,
      { display_title: 'AI 填充属性' },
    )
    expect(descriptor.presentationId).toBe('presentation_a')
    expect(descriptor.conversationId).toBe('conversation_a')
    expect(descriptor.status).toBe('reserved')
  })

  it('fetchAiPresentationStatus GET 状态端点：只含展示元数据', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: { ...DESCRIPTOR, status: 'completed' },
      status: 200,
    })

    const descriptor = await fetchAiPresentationStatus('presentation_a')

    expect(apiClient.get).toHaveBeenCalledWith(
      `${AI_PRESENTATIONS_PATH}/presentation_a`,
    )
    expect(descriptor.status).toBe('completed')
  })

  it('observe Chat 预置首次 user 消息，并以 presentation_id reconnect', async () => {
    // observe reconnect：204 = 没有可用流（Vercel reconnect 约定）。
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 204, body: null }))

    const chat = createPresentationObserveChat('presentation_a', {
      initialUserMessage: 'hello',
    })
    expect(chat.id).toBe('presentation_a')
    expect(chat.messages).toEqual([{
      id: 'presentation_a:initial-user',
      role: 'user',
      parts: [{ type: 'text', text: 'hello' }],
    }])

    // 204 → reconnect 为 null：resumeStream 干净结束，不消费任何 chunk。
    await chat.resumeStream()

    expect(vi.mocked(globalThis.fetch)).toHaveBeenCalledWith(
      presentationStreamPath('presentation_a'),
      expect.objectContaining({ method: 'GET' }),
    )
    expect(presentationStreamPath('presentation_a')).toBe(
      `${AI_PRESENTATIONS_PATH}/presentation_a/stream`,
    )
  })

  it('assistant SSE 到达后仍保留预置的首次 user 消息', async () => {
    const encoder = new TextEncoder()
    const chunks = [
      { type: 'start', messageId: 'assistant-1' },
      { type: 'start-step' },
      { type: 'text-start', id: 'text-1' },
      { type: 'text-delta', id: 'text-1', delta: 'Hi!' },
      { type: 'text-end', id: 'text-1' },
      { type: 'finish-step' },
      { type: 'finish', finishReason: 'stop' },
    ]
    const body = chunks.map((chunk) => `data: ${JSON.stringify(chunk)}\n\n`).join('')
      + 'data: [DONE]\n\n'
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(
      new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(encoder.encode(body))
          controller.close()
        },
      }),
      {
        status: 200,
        headers: {
          'Content-Type': 'text/event-stream',
          'x-vercel-ai-ui-message-stream': 'v1',
        },
      },
    )))

    const chat = createPresentationObserveChat('presentation_stream', {
      initialUserMessage: 'hello',
    })
    await chat.resumeStream()

    expect(chat.messages).toHaveLength(2)
    expect(chat.messages[0]).toMatchObject({
      role: 'user',
      parts: [{ type: 'text', text: 'hello' }],
    })
    expect(chat.messages[1]).toMatchObject({
      id: 'assistant-1',
      role: 'assistant',
    })
  })

  it('observe fetch 非 2xx 时解析后端标准 JSON 错误并附加 code/status', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: false,
      status: 404,
      json: async () => ({
        error: 'AI 展示不存在或已过期。',
        error_code: 'AI_PRESENTATION_NOT_FOUND',
      }),
    }))
    let observed: (Error & { code?: string; status?: number }) | null = null
    const chat = createPresentationObserveChat('presentation_missing', {
      onError: (error) => {
        observed = error as Error & { code?: string; status?: number }
      },
    })

    await chat.resumeStream().catch((error: unknown) => {
      observed = error as Error & { code?: string; status?: number }
    })
    await Promise.resolve()
    const failure = observed || (chat.error as (Error & { code?: string; status?: number }) | null)

    expect(failure?.message).toContain('AI 展示不存在或已过期。')
    expect(failure?.code).toBe('AI_PRESENTATION_NOT_FOUND')
    expect(failure?.status).toBe(404)
  })

  it('describePresentationStatus 覆盖全部展示状态', () => {
    expect(describePresentationStatus('reserved')).toBe('已预留')
    expect(describePresentationStatus('bound')).toBe('已绑定')
    expect(describePresentationStatus('running')).toBe('运行中')
    expect(describePresentationStatus('finalizing')).toBe('收尾中')
    expect(describePresentationStatus('completed')).toBe('已完成')
    expect(describePresentationStatus('failed')).toBe('失败')
    expect(describePresentationStatus('expired')).toBe('已过期')
  })
})
