import { defineComponent, effectScope, h, nextTick, reactive, ref } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createDefaultPricingInput, createEmptyDraftDetail, createEmptyDraftProductContext } from '@/constants/initialState'
import { loadDraft } from '@/api/workflow/catalog'
import type { DraftMutationResponse } from '@/api/workflow/normalizers'
import { useAiDraftSync } from '../useAiDraftSync'
import PricingPanel from '@/components/domain/PricingPanel.vue'

const state = vi.hoisted(() => ({ chat: null as unknown }))
vi.mock('@/stores/aiChat', () => ({ useAiChatStore: () => state.chat }))
vi.mock('@/api/workflow/catalog', () => ({ loadDraft: vi.fn() }))
const scopes: ReturnType<typeof effectScope>[] = []

async function setup() {
  const chat = reactive({ activeConversationId: 'chat-1', historyVersion: 1, isBusy: false })
  state.chat = chat
  const draft = ref(createEmptyDraftDetail('yandex'))
  draft.value.draftId = 'draft-1'
  draft.value.updatedAt = 'v1'
  draft.value.title = '原始标题'
  const input = ref(createDefaultPricingInput())
  input.value.otherCostCny = 30
  const loading = ref(false)
  const clearError = vi.fn()
  const accept = vi.fn((result: DraftMutationResponse) => {
    draft.value = result.draft
    input.value.otherCostCny = Number(result.draft.pricing.common && (result.draft.pricing.common as Record<string, unknown>).other_cost_cny || 30)
  })
  const scope = effectScope()
  scopes.push(scope)
  const sync = scope.run(() => useAiDraftSync({ draft, input, loading, accept, clearError }))!
  await nextTick()
  const saved = JSON.parse(JSON.stringify(draft.value))
  saved.updatedAt = 'v2'
  saved.title = 'AI 已保存的标题'
  saved.pricing = { common: { other_cost_cny: 40 } }
  saved.skuItems = [{ sku_id: 'sku-1', selected: true, overrides: {}, pricing: { applied: true }, attributes_by_target: {}, publications: {}, stock: '1', sku: 'sku-1' }]
  const response: DraftMutationResponse = { ok: true, draft: saved, productContext: createEmptyDraftProductContext(), productsIndex: [], draftsIndex: [], raw: {} }
  vi.mocked(loadDraft).mockResolvedValue(response)
  return { chat, draft, input, loading, clearError, accept, sync, response, scope }
}

describe('AI 修改后的草稿同步', () => {
  beforeEach(() => vi.clearAllMocks())
  afterEach(() => { scopes.splice(0).forEach(scope => scope.stop()) })

  it('历史提交后读取最新草稿，同时同步内容、核价参数和版本', async () => {
    const { chat, sync, draft, input, accept } = await setup()
    chat.historyVersion++
    await flushPromises()
    expect(loadDraft).toHaveBeenCalledWith('draft-1')
    expect(accept).toHaveBeenCalledOnce()
    expect(draft.value.updatedAt).toBe('v2')
    expect(draft.value.title).toBe('AI 已保存的标题')
    expect(input.value.otherCostCny).toBe(40)
    expect(draft.value.skuItems[0]?.pricing.applied).toBe(true)
    expect(sync.dirty.value).toBe(false)
  })

  it('实际核价组件先收到加载状态，系统回填不会把已应用售价变成预览', async () => {
    const { chat, draft, input, loading } = await setup()
    const wrapper = mount(defineComponent({ setup: () => () => h(PricingPanel, {
      input: input.value, result: null, draftItems: [], draftId: draft.value.draftId, draftTitle: draft.value.title,
      productContext: createEmptyDraftProductContext(), platformOptions: [], loading: loading.value, skuItems: draft.value.skuItems,
    }) }))
    chat.historyVersion++
    await flushPromises()
    expect(draft.value.skuItems[0]?.pricing.applied).toBe(true)
    wrapper.unmount()
  })

  it.each(['title', 'pricing', 'sku'] as const)('保留未保存的 %s，确认重新加载后才替换', async field => {
    const { chat, draft, input, sync, accept } = await setup()
    if (field === 'title') draft.value.title = '手工标题'
    if (field === 'pricing') input.value.otherCostCny = 99
    if (field === 'sku') draft.value.skuItems.push({ sku_id: 'local', selected: true, sku: '', stock: '8', overrides: {}, attributes_by_target: {}, pricing: {}, publications: {} })
    chat.historyVersion++
    await flushPromises()
    expect(accept).not.toHaveBeenCalled()
    expect(draft.value.updatedAt).toBe('v1')
    expect(sync.pending.value).toBe(true)
    expect(sync.message.value).toContain('未保存')
    await sync.refresh(true)
    expect(draft.value.updatedAt).toBe('v2')
    expect(sync.dirty.value).toBe(false)
    expect(sync.pending.value).toBe(false)
  })

  it('读取期间新输入的内容也不能被自动覆盖', async () => {
    const { sync, input, response, accept } = await setup()
    let resolve!: (value: DraftMutationResponse) => void
    vi.mocked(loadDraft).mockReturnValue(new Promise(done => { resolve = done }))
    const request = sync.refresh()
    input.value.otherCostCny = 99
    resolve(response)
    await request
    expect(input.value.otherCostCny).toBe(99)
    expect(accept).not.toHaveBeenCalled()
    expect(sync.pending.value).toBe(true)
  })

  it('重新加载期间的新编辑仍保留，不能把点击时的确认扩展到后续输入', async () => {
    const { sync, draft, response, accept } = await setup()
    let resolve!: (value: DraftMutationResponse) => void
    vi.mocked(loadDraft).mockReturnValue(new Promise(done => { resolve = done }))
    const request = sync.refresh(true)
    draft.value.title = '请求发出后的输入'
    resolve(response)
    await request
    expect(accept).not.toHaveBeenCalled()
    expect(draft.value.title).toBe('请求发出后的输入')
  })

  it('切换草稿后丢弃旧响应', async () => {
    const { sync, draft, response, accept } = await setup()
    let resolve!: (value: DraftMutationResponse) => void
    vi.mocked(loadDraft).mockReturnValue(new Promise(done => { resolve = done }))
    const request = sync.refresh()
    draft.value = { ...createEmptyDraftDetail(), draftId: 'draft-2' }
    resolve(response)
    await request
    expect(accept).not.toHaveBeenCalled()
    expect(draft.value.draftId).toBe('draft-2')
  })

  it('已有操作推进保存版本时丢弃旧读取结果', async () => {
    const { sync, draft, response, accept } = await setup()
    let resolve!: (value: DraftMutationResponse) => void
    vi.mocked(loadDraft).mockReturnValue(new Promise(done => { resolve = done }))
    const request = sync.refresh()
    draft.value.updatedAt = 'v3'
    resolve(response)
    await request
    expect(accept).not.toHaveBeenCalled()
    expect(draft.value.updatedAt).toBe('v3')
  })

  it('保存文本不会把未应用的核价输入当成已保存', async () => {
    const { chat, sync, draft, input, accept } = await setup()
    input.value.otherCostCny = 99
    draft.value = { ...draft.value, title: '已保存文本', updatedAt: 'v1a' }
    await nextTick()
    await nextTick()
    expect(sync.dirty.value).toBe(true)
    chat.historyVersion++
    await flushPromises()
    expect(accept).not.toHaveBeenCalled()
  })

  it('操作进行中延后刷新，并读取期间到达的后续提交', async () => {
    const { chat, sync, loading, response } = await setup()
    loading.value = true
    chat.historyVersion++
    await nextTick()
    expect(loadDraft).not.toHaveBeenCalled()
    loading.value = false
    await flushPromises()
    expect(loadDraft).toHaveBeenCalledOnce()
    vi.mocked(loadDraft).mockResolvedValue({ ...response, draft: { ...response.draft, updatedAt: 'v3' } })
    chat.historyVersion++
    await flushPromises()
    expect(loadDraft).toHaveBeenCalledTimes(2)
    expect(sync.pending.value).toBe(false)
  })

  it('工具部分写入后回合失败，流结束仍检查业务数据；读取失败可重试', async () => {
    const { chat, sync, draft } = await setup()
    chat.isBusy = true
    await nextTick()
    vi.mocked(loadDraft).mockRejectedValueOnce(new Error('网络中断'))
    chat.isBusy = false
    await flushPromises()
    expect(sync.message.value).toContain('网络中断')
    expect(draft.value.updatedAt).toBe('v1')
    await sync.refresh(true)
    expect(sync.message.value).toBe('')
    expect(draft.value.updatedAt).toBe('v2')
  })

  it('同版本的查询回合不会替换表单或清空错误', async () => {
    const { chat, draft, response, accept, clearError } = await setup()
    vi.mocked(loadDraft).mockResolvedValue({ ...response, draft: { ...response.draft, updatedAt: draft.value.updatedAt } })
    chat.historyVersion++
    await flushPromises()
    expect(accept).not.toHaveBeenCalled()
    expect(clearError).not.toHaveBeenCalled()
  })
})
