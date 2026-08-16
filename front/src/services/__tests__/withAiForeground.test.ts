import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { apiClient } from '@/api/client'
import { withAiForeground } from '@/services/withAiForeground'
import { useAiWorkDisplayStore } from '@/stores/aiWorkDisplay'

vi.mock('@/api/client', () => ({
  API_REQUEST_TIMEOUT_MS: 30000,
  apiClient: {
    post: vi.fn(),
    get: vi.fn(),
  },
}))

const DESCRIPTOR = {
  presentation_id: 'presentation_w',
  conversation_id: 'conversation_w',
  display_title: 'AI 测试任务',
  status: 'reserved',
}

function stubNoStream(): void {
  // observe reconnect：204 = 没有可用流（Vercel reconnect 约定），跳过流消费。
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 204, body: null }))
}

describe('withAiForeground 通用前台 wrapper', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    setActivePinia(createPinia())
    stubNoStream()
  })

  it('reserve 后注入 presentationId 给业务操作，返回原业务类型化值', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({ data: DESCRIPTOR, status: 200 })

    const result = await withAiForeground(
      { displayTitle: 'AI 测试任务' },
      ({ presentationId }) => Promise.resolve({ presentationId, value: 42 }),
    )

    expect(result).toEqual({ presentationId: 'presentation_w', value: 42 })
    expect(apiClient.post).toHaveBeenCalledWith(
      '/api/v1/ai-presentations',
      { display_title: 'AI 测试任务' },
    )
  })

  it('SSE 与业务请求并发启动：业务不等待展示流完成', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({ data: DESCRIPTOR, status: 200 })
    let streamFetchStarted = false
    let streamFetchResolved = false
    let resolveStreamFetch: (value: unknown) => void = () => {}
    vi.stubGlobal('fetch', vi.fn(() => {
      streamFetchStarted = true
      return new Promise((resolve) => {
        resolveStreamFetch = (value) => {
          streamFetchResolved = true
          resolve(value)
        }
      })
    }))

    let streamPendingWhenBusinessRan = false
    let operationDone = false
    const pending = withAiForeground(
      { displayTitle: 'AI 测试任务' },
      async ({ presentationId }) => {
        // SDK 内部经过若干微任务后才发出展示流 fetch；给并发启动一个有界窗口。
        for (let index = 0; index < 2000 && !streamFetchStarted; index += 1) {
          await Promise.resolve()
        }
        // 业务结束时展示流已发起且尚未收尾：两者并发，业务不等待流完成。
        streamPendingWhenBusinessRan = streamFetchStarted && !streamFetchResolved
        operationDone = true
        return { presentationId }
      },
    )

    // 业务操作记录完状态后才让展示流以 204 收尾，避免与状态采集竞争。
    for (let index = 0; index < 2000 && !operationDone; index += 1) {
      await Promise.resolve()
    }
    resolveStreamFetch({ ok: true, status: 204, body: null })

    const result = await pending
    expect(streamFetchStarted).toBe(true)
    expect(streamPendingWhenBusinessRan).toBe(true)
    expect(result.presentationId).toBe('presentation_w')
  })

  it('业务成功但 SSE 失败：仍返回成功结果并恢复 global-chat', async () => {
    const display = useAiWorkDisplayStore()
    vi.mocked(apiClient.post).mockResolvedValueOnce({ data: DESCRIPTOR, status: 200 })
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))

    const result = await withAiForeground(
      { displayTitle: 'AI 测试任务' },
      () => new Promise((resolve) => {
        setTimeout(() => resolve({ ok: true }), 20)
      }),
    )

    expect(result).toEqual({ ok: true })
    expect(display.displayMode).toBe('global-chat')
    expect(display.foregroundOccupied).toBe(false)
    expect(display.terminalNotice?.kind).toBe('success')
    expect(display.terminalNotice?.text).toBe('AI 测试任务完成')
  })

  it('业务失败但 SSE 正常：抛业务错误并恢复 global-chat（不改写为成功）', async () => {
    const display = useAiWorkDisplayStore()
    vi.mocked(apiClient.post).mockResolvedValueOnce({ data: DESCRIPTOR, status: 200 })

    await expect(withAiForeground(
      {
        displayTitle: 'AI 测试任务',
        failureNotice: (error) => `自定义提示：${(error as Error).message}`,
      },
      () => Promise.reject(new Error('业务校验失败')),
    )).rejects.toThrow('业务校验失败')

    expect(display.displayMode).toBe('global-chat')
    expect(display.foregroundOccupied).toBe(false)
    expect(display.terminalNotice?.kind).toBe('failure')
    expect(display.terminalNotice?.text).toBe('自定义提示：业务校验失败')
  })

  it('reserve 失败：同步占用释放，业务操作不运行', async () => {
    const display = useAiWorkDisplayStore()
    vi.mocked(apiClient.post).mockRejectedValueOnce(new Error('网络不可用'))
    const operation = vi.fn(() => Promise.resolve('never'))

    await expect(withAiForeground({ displayTitle: 'AI 测试任务' }, operation))
      .rejects.toThrow('网络不可用')

    expect(operation).not.toHaveBeenCalled()
    expect(display.foregroundOccupied).toBe(false)
    expect(display.foregroundStartPending).toBe(false)
    expect(display.displayMode).toBe('global-chat')
  })

  it('已有前台 presentation 时同步拒绝：reserve 与业务请求都不发出', async () => {
    const display = useAiWorkDisplayStore()
    display.beginForegroundStart()
    const operation = vi.fn(() => Promise.resolve('never'))

    await expect(withAiForeground({ displayTitle: 'AI 测试任务' }, operation))
      .rejects.toThrow('已有前台 AI 任务运行')

    expect(operation).not.toHaveBeenCalled()
    expect(apiClient.post).not.toHaveBeenCalled()
    // 原有占用不受影响。
    expect(display.foregroundOccupied).toBe(true)
  })

  it('运行期间前台被接管：业务操作中可观察到 observe Chat，成功后恢复', async () => {
    const display = useAiWorkDisplayStore()
    vi.mocked(apiClient.post).mockResolvedValueOnce({ data: DESCRIPTOR, status: 200 })
    let modeDuringOperation = ''
    let chatIdDuringOperation = ''

    await withAiForeground(
      { displayTitle: 'AI 测试任务', successNotice: () => '自定义成功提示' },
      async () => {
        modeDuringOperation = display.displayMode
        chatIdDuringOperation = display.foregroundPresentation?.chat.id ?? ''
        return null
      },
    )

    expect(modeDuringOperation).toBe('presentation')
    expect(chatIdDuringOperation).toBe('presentation_w')
    expect(display.displayMode).toBe('global-chat')
    expect(display.terminalNotice?.kind).toBe('success')
    expect(display.terminalNotice?.text).toBe('自定义成功提示')
    expect(display.presentationVersion).toBe(1)
  })

  it('无 Agent 路径：流确定关闭（204）后业务 response 正常收尾，不等待不存在的 Agent', async () => {
    const display = useAiWorkDisplayStore()
    vi.mocked(apiClient.post).mockResolvedValueOnce({ data: DESCRIPTOR, status: 200 })

    const result = await withAiForeground(
      { displayTitle: 'AI 填充属性' },
      () => Promise.resolve({ source: 'rules', warning: '无可填属性' }),
    )

    expect(result).toEqual({ source: 'rules', warning: '无可填属性' })
    expect(display.displayMode).toBe('global-chat')
    expect(display.foregroundOccupied).toBe(false)
    expect(display.terminalNotice?.kind).toBe('success')
  })
})
