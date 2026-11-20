import { beforeEach, describe, expect, it, vi } from 'vitest'
import { apiClient } from '@/api/client'
import {
  CATEGORY_MATCH_PATH,
  CATEGORY_MATCH_REQUEST_TIMEOUT_MS,
  fetchCategoryAttrs,
  matchCategories,
} from '@/api/workflow/publishing'
import { createEmptyDraftDetail } from '@/constants/initialState'

vi.mock('@/api/client', () => ({
  API_REQUEST_TIMEOUT_MS: 30000,
  apiClient: {
    post: vi.fn(),
    get: vi.fn(),
  },
}))

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

describe('类目匹配与普通对话关联', () => {
  beforeEach(() => vi.clearAllMocks())

  it('直接请求类目接口，传输层携带普通对话标识', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({ data: TYPED_RESULT, status: 200 })
    const [result] = await matchCategories(draft(), [target()], 'conversation-a')
    expect(apiClient.post).toHaveBeenCalledOnce()
    expect(apiClient.post).toHaveBeenCalledWith(CATEGORY_MATCH_PATH, expect.objectContaining({
      draft_id: 'draft-1', platform: 'mercadolibre', site: 'MLM', language: 'es-MX',
    }), { aiConversationId: 'conversation-a', timeout: CATEGORY_MATCH_REQUEST_TIMEOUT_MS })
    expect(CATEGORY_MATCH_REQUEST_TIMEOUT_MS).toBeGreaterThan(150_000)
    expect(result.candidates.map(item => item.id)).toEqual(['MLM-FAN', 'MLM-USB'])
    expect(result.candidates[0]?.raw.matchEvidence).toEqual(['主体一致'])
    expect(result.trace.taskRunId).toBe('task-1')
  })

  it('多个目标共用一次业务提交和对话，保留逐目标结果', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: { items: [TYPED_RESULT, { ok: false, status: 'unresolved', candidates: [] }] },
    })
    const rows = await matchCategories(draft(), [target(), { ...target(), site: 'MLB' }], 'conversation-a')
    expect(apiClient.post).toHaveBeenCalledOnce()
    expect(apiClient.post).toHaveBeenCalledWith(CATEGORY_MATCH_PATH + '/batch', {
      draft_id: 'draft-1',
      targets: [expect.objectContaining({ site: 'MLM' }), expect.objectContaining({ site: 'MLB' })],
    }, { aiConversationId: 'conversation-a', timeout: CATEGORY_MATCH_REQUEST_TIMEOUT_MS * 2 })
    expect(rows.map(row => row.status)).toEqual(['completed', 'unresolved'])
  })

  it('保留 Ozon 候选的 type_id 与 description_category_id 供类目选择读取', async () => {
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
    const [result] = await matchCategories(ozonDraft, [{
      platform: 'ozon',
      site: 'global',
      language: 'ru-RU',
      listingCurrency: 'RUB',
    }], 'conversation-a')
    const selected = result.candidates[0]

    expect(selected?.raw).toEqual(expect.objectContaining({
      type_id: '971326576',
      description_category_id: '17039878',
    }))
    // selectCategory 的 Ozon 取值契约：type_id 是发布类目，description_category_id 是配对目录。
    expect(String(selected?.raw.type_id || selected?.id || '').trim()).toBe('971326576')
    expect(String(selected?.raw.description_category_id || '').trim()).toBe('17039878')
  })

})

describe('Mercado 类目属性编辑契约', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('平台文本格式和填写提示透传到编辑定义', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: { ok: true, category_id: 'pet-toys', attributes: [{
        id: 'extra', name: '其他属性', required: false, value_type: 'text',
        text_format: 'name_value_lines', format_hint: '每行填写“属性名:属性值”',
      }] },
    })
    const result = await fetchCategoryAttrs('yandex', 'pet-toys', 'global')
    expect(result.optionalAttributes[0]).toEqual(expect.objectContaining({
      textFormat: 'name_value_lines', formatHint: '每行填写“属性名:属性值”',
    }))
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
