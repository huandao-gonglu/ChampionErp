import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { apiClient } from '@/api/client'
import {
  CATEGORY_MATCH_PATH,
  CATEGORY_MATCH_REQUEST_TIMEOUT_MS,
  fetchCategoryAttrs,
  matchCategory,
} from '@/api/workflow/publishing'
import { AI_PRESENTATIONS_PATH } from '@/api/aiPresentations'
import { useAiWorkDisplayStore } from '@/stores/aiWorkDisplay'
import { createEmptyDraftDetail } from '@/constants/initialState'

vi.mock('@/api/client', () => ({
  API_REQUEST_TIMEOUT_MS: 30000,
  apiClient: {
    post: vi.fn(),
    get: vi.fn(),
  },
}))

const DESCRIPTOR = {
  presentation_id: 'presentation_abc',
  conversation_id: 'conversation_abc',
  display_title: 'AI 匹配类目',
  status: 'reserved',
}

const TYPED_RESULT = {
  ok: true,
  status: 'completed',
  selected_category_id: 'MLM-FAN',
  candidates: [
    {
      category_id: 'MLM-USB',
      name: 'Accesorios USB',
      path_segments: ['Electrónica', 'Accesorios USB'],
    },
    {
      category_id: 'MLM-FAN',
      name: 'Ventiladores',
      path_segments: ['Hogar', 'Ventiladores'],
    },
  ],
  query: 'ventilador',
  decision: {
    confidence_band: 'high',
    model_confidence: 0.95,
    decision_score: 0.86,
    abstained: false,
    evidence: ['主体一致'],
    search_count: 1,
  },
  failure: null,
  trace: {
    task_run_id: 'task-1',
  },
}

function target() {
  return {
    platform: 'mercadolibre' as const,
    site: 'MLM',
    language: 'es-MX',
    listingCurrency: 'MXN',
  }
}

function draft() {
  const empty = createEmptyDraftDetail('mercadolibre')
  empty.draftId = 'draft-1'
  empty.productId = 'product-1'
  return empty
}

function stubObserveStream(): void {
  // observe reconnect：204 = 没有可用流（Vercel reconnect 约定），跳过流消费。
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 204, body: null }))
}

describe('category.product_match 同步 focused response + 通用 presentation 契约', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    setActivePinia(createPinia())
    stubObserveStream()
  })

  it('reserve 走通用 presentation endpoint，业务请求走同步 focused endpoint 并保留类型化结果 shape', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({ data: DESCRIPTOR, status: 200 })
    vi.mocked(apiClient.post).mockResolvedValueOnce({ data: TYPED_RESULT, status: 200 })

    const result = await matchCategory(draft(), target())

    // 第一个 POST 是 reserve（只携带 display_title，不携带任何业务字段）。
    expect(apiClient.post).toHaveBeenNthCalledWith(
      1,
      AI_PRESENTATIONS_PATH,
      { display_title: 'AI 匹配类目' },
    )
    // 第二个 POST 是同步业务请求；timeout 必须大于后端 60s Agent deadline。
    expect(apiClient.post).toHaveBeenNthCalledWith(
      2,
      CATEGORY_MATCH_PATH,
      expect.objectContaining({
        draft_id: 'draft-1',
        platform: 'mercadolibre',
        site: 'MLM',
        language: 'es-MX',
      }),
      expect.objectContaining({
        aiPresentationId: 'presentation_abc',
        timeout: CATEGORY_MATCH_REQUEST_TIMEOUT_MS,
      }),
    )
    expect(CATEGORY_MATCH_REQUEST_TIMEOUT_MS).toBeGreaterThan(60_000)
    // presentation 关联只走传输层 option/header：不进入业务 JSON。
    const businessBody = vi.mocked(apiClient.post).mock.calls[1]?.[1] as Record<string, unknown>
    expect(businessBody).not.toHaveProperty('presentation_id')
    expect(businessBody).not.toHaveProperty('aiPresentationId')

    expect(result.status).toBe('completed')
    expect(result.query).toBe('ventilador')
    expect(result.candidates.map((item) => item.id)).toEqual(['MLM-FAN', 'MLM-USB'])
    expect(result.candidates[0]?.path).toBe('Hogar / Ventiladores')
    expect(result.decision.confidenceBand).toBe('high')
    expect(result.trace.taskRunId).toBe('task-1')
  })

  it('保留 Ozon 候选的 type_id 与 description_category_id 供类目选择读取', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({ data: DESCRIPTOR, status: 200 })
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: {
        ...TYPED_RESULT,
        selected_category_id: '971326576',
        candidates: [{
          category_id: '971326576',
          name: 'Автомагнитолы',
          path_segments: ['Автотовары', 'Автомагнитолы'],
          type_id: '971326576',
          description_category_id: '17039878',
        }],
      },
      status: 200,
    })

    const ozonDraft = createEmptyDraftDetail('ozon')
    ozonDraft.draftId = 'draft-ozon'
    ozonDraft.productId = 'product-ozon'
    const result = await matchCategory(ozonDraft, {
      platform: 'ozon',
      site: 'global',
      language: 'ru-RU',
      listingCurrency: 'RUB',
    })
    const selected = result.candidates[0]

    expect(selected?.raw).toEqual(expect.objectContaining({
      type_id: '971326576',
      description_category_id: '17039878',
    }))
    // selectCategory 的 Ozon 取值契约：type_id 是发布类目，description_category_id 是配对目录。
    expect(String(selected?.raw.type_id || selected?.id || '').trim()).toBe('971326576')
    expect(String(selected?.raw.description_category_id || '').trim()).toBe('17039878')
  })

  it('运行期间临时接管浮窗，业务终态后恢复 global-chat', async () => {
    const display = useAiWorkDisplayStore()
    let modeDuringBusiness = ''
    vi.mocked(apiClient.post).mockResolvedValueOnce({ data: DESCRIPTOR, status: 200 })
    vi.mocked(apiClient.post).mockImplementationOnce(async () => {
      modeDuringBusiness = display.displayMode
      return { data: TYPED_RESULT, status: 200 }
    })

    await matchCategory(draft(), target())

    // 业务请求期间前台 presentation 仍在展示（不得提前恢复）。
    expect(modeDuringBusiness).toBe('presentation')
    expect(display.foregroundPresentation).toBeNull()
    expect(display.displayMode).toBe('global-chat')
    expect(display.terminalNotice?.kind).toBe('success')
    expect(display.terminalNotice?.text).toBe('类目匹配完成')
    expect(display.presentationVersion).toBe(1)
  })

  it('observe Chat 以 presentation_id 为 id 并消费 presentation observe 流端点', async () => {
    const display = useAiWorkDisplayStore()
    let observeChatId = ''
    vi.mocked(apiClient.post).mockResolvedValueOnce({ data: DESCRIPTOR, status: 200 })
    vi.mocked(apiClient.post).mockImplementationOnce(async () => {
      observeChatId = display.foregroundPresentation?.chat.id || ''
      return { data: TYPED_RESULT, status: 200 }
    })

    await matchCategory(draft(), target())

    expect(observeChatId).toBe('presentation_abc')
    // resumeStream 的 reconnect GET 指向通用 presentation observe 端点。
    const fetchMock = vi.mocked(globalThis.fetch)
    expect(fetchMock).toHaveBeenCalledWith(
      `${AI_PRESENTATIONS_PATH}/presentation_abc/stream`,
      expect.objectContaining({ method: 'GET' }),
    )
    // observe Chat 从不发送伪造用户消息：所有请求都是 GET /stream。
    for (const call of fetchMock.mock.calls) {
      const url = String(call[0])
      const init = call[1] as RequestInit | undefined
      expect(init?.method ?? 'GET').toBe('GET')
      expect(url.endsWith('/stream')).toBe(true)
    }
  })

  it('业务判断失败（ok=false）仍是合法 200 类型化结果：不抛错，caller 读取 failure', async () => {
    const display = useAiWorkDisplayStore()
    vi.mocked(apiClient.post).mockResolvedValueOnce({ data: DESCRIPTOR, status: 200 })
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: {
        ...TYPED_RESULT,
        ok: false,
        status: 'failed',
        selected_category_id: '',
        failure: {
          code: 'MODEL_RESPONSE_SCHEMA_INVALID',
          message: '模型输出未通过校验',
          stage: 'model',
          retryable: false,
        },
      },
      status: 200,
    })

    const result = await matchCategory(draft(), target())

    expect(result.ok).toBe(false)
    expect(result.status).toBe('failed')
    expect(result.failure?.code).toBe('MODEL_RESPONSE_SCHEMA_INVALID')
    expect(display.displayMode).toBe('global-chat')
    // 未抛错的合法业务 response 走成功收尾：提示由 successNotice 解释结果。
    expect(display.terminalNotice?.kind).toBe('success')
    expect(display.terminalNotice?.text).toBe('类目匹配结束，仍需人工确认候选')
  })

  it('已有前台 presentation 时第二次触发被明确拒绝', async () => {
    const display = useAiWorkDisplayStore()
    display.attachForegroundPresentation(
      {
        presentationId: 'presentation_existing',
        conversationId: 'conversation_existing',
        displayTitle: 'AI 填充属性',
        status: 'running',
      },
      // 仅用于占位：测试不消费该 Chat。
      { id: 'presentation_existing', messages: [] } as never,
    )

    await expect(matchCategory(draft(), target())).rejects.toThrow('已有前台 AI 任务运行')
    expect(apiClient.post).not.toHaveBeenCalled()
  })

  it('并发双触发原子占用：只发出一个业务请求', async () => {
    let resolveReserve: (value: unknown) => void = () => {}
    // reserve 挂起：第一次触发已同步占用前台但尚未拿到 presentation。
    vi.mocked(apiClient.post).mockImplementationOnce(
      () => new Promise((resolve) => {
        resolveReserve = resolve
      }),
    )
    vi.mocked(apiClient.post).mockResolvedValueOnce({ data: TYPED_RESULT, status: 200 })

    const first = matchCategory(draft(), target())
    await expect(matchCategory(draft(), target())).rejects.toThrow('已有前台 AI 任务运行')

    resolveReserve({ data: DESCRIPTOR, status: 200 })
    const result = await first

    // 第二个触发在 reserve 之前被同步拒绝：只预留一个 presentation、只发一个业务请求。
    expect(apiClient.post).toHaveBeenCalledTimes(2)
    expect(vi.mocked(apiClient.post).mock.calls[0]?.[0]).toBe(AI_PRESENTATIONS_PATH)
    expect(vi.mocked(apiClient.post).mock.calls[1]?.[0]).toBe(CATEGORY_MATCH_PATH)
    expect(result.ok).toBe(true)
  })

  it('reserve POST 失败释放同步占用：随后触发可以重新启动', async () => {
    const display = useAiWorkDisplayStore()
    vi.mocked(apiClient.post).mockRejectedValueOnce(new Error('网络不可用'))

    await expect(matchCategory(draft(), target())).rejects.toThrow('网络不可用')

    // 失败后占用被释放，不留下永久 busy。
    expect(display.foregroundOccupied).toBe(false)
    expect(display.foregroundStartPending).toBe(false)

    vi.mocked(apiClient.post).mockResolvedValueOnce({ data: DESCRIPTOR, status: 200 })
    vi.mocked(apiClient.post).mockResolvedValueOnce({ data: TYPED_RESULT, status: 200 })
    const result = await matchCategory(draft(), target())

    expect(result.ok).toBe(true)
    expect(display.foregroundOccupied).toBe(false)
  })

  it('业务请求失败：抛出业务错误并恢复 global-chat（SSE 正常也不改写为成功）', async () => {
    const display = useAiWorkDisplayStore()
    vi.mocked(apiClient.post).mockResolvedValueOnce({ data: DESCRIPTOR, status: 200 })
    const businessError = Object.assign(new Error('业务 Agent 运行失败。'), {
      code: 'AI_BUSINESS_RUN_FAILED',
      status: 502,
    })
    vi.mocked(apiClient.post).mockRejectedValueOnce(businessError)

    await expect(matchCategory(draft(), target())).rejects.toThrow('业务 Agent 运行失败。')

    expect(display.displayMode).toBe('global-chat')
    expect(display.foregroundOccupied).toBe(false)
    expect(display.terminalNotice?.kind).toBe('failure')
    expect(display.terminalNotice?.text).toBe('业务 Agent 运行失败。')
  })
})

describe('展示连接中断不改变业务语义（展示链与业务链分离）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    setActivePinia(createPinia())
    // observe 流连接直接失败（模拟 SSE 断连/网络故障）。
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))
  })

  it('SSE 断连但业务成功：返回成功结果并恢复 global-chat', async () => {
    const display = useAiWorkDisplayStore()
    let displayErrorDuringBusiness = ''
    let secondTriggerRejected = false
    vi.mocked(apiClient.post).mockResolvedValueOnce({ data: DESCRIPTOR, status: 200 })
    vi.mocked(apiClient.post).mockImplementationOnce(async () => {
      // 等待展示失败先落盘（展示断连只降级提示，不中断业务等待）。
      await new Promise((resolve) => {
        setTimeout(resolve, 20)
      })
      displayErrorDuringBusiness = display.foregroundPresentation?.error?.message ?? ''
      // 业务等待期间单前台不变量继续有效：第二次触发被拒绝。
      try {
        await matchCategory(draft(), target())
      } catch (error) {
        secondTriggerRejected = String((error as Error).message).includes(
          '已有前台 AI 任务运行',
        )
      }
      return { data: TYPED_RESULT, status: 200 }
    })

    const result = await matchCategory(draft(), target())

    expect(displayErrorDuringBusiness).toContain('实时展示连接中断')
    expect(secondTriggerRejected).toBe(true)
    // 第二次触发没有发起新的 reserve/业务请求（reserve 1 + 业务 1）。
    expect(apiClient.post).toHaveBeenCalledTimes(2)
    // 业务结果由同步 response 交付：SSE 断连不改写为失败。
    expect(result.ok).toBe(true)
    expect(result.selectedCategoryId).toBe('MLM-FAN')
    expect(display.displayMode).toBe('global-chat')
    expect(display.terminalNotice?.kind).toBe('success')
    expect(display.presentationVersion).toBe(1)
  })
})

describe('Mercado 类目属性编辑契约', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('保留 open enum、collection 与有界预览标记', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: {
        ok: true,
        category_id: 'CBT455865',
        category_path: 'Computers / Portable Fans',
        attributes: [{
          id: 'COMPATIBLE_DEVICES',
          name: 'Compatible devices',
          required: true,
          value_type: 'string',
          value_mode: 'open_enum',
          allow_custom_values: true,
          is_collection: true,
          has_more_values: true,
          options: ['Phone'],
        }],
      },
    })

    const result = await fetchCategoryAttrs('mercadolibre', 'CBT455865', 'CBT')

    expect(result.requiredAttributes[0]).toEqual(expect.objectContaining({
      valueType: 'string',
      valueMode: 'open_enum',
      allowCustomValues: true,
      isCollection: true,
      hasMoreValues: true,
      options: ['Phone'],
    }))
  })
})
