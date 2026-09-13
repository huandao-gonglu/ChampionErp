import { effectScope, nextTick, reactive, ref } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createEmptyDraftDetail } from '@/constants/initialState'
import { useAiAttributeResults } from '../useAiAttributeResults'

const state = vi.hoisted(() => ({ chat: null as unknown }))
vi.mock('@/stores/aiChat', () => ({ useAiChatStore: () => state.chat }))

function receipt(overrides = {}) {
  return { type: 'tool-product_attributes_update', toolCallId: 'saved', state: 'output-available', output: {
    changed: true, previous_updated_at: 'before', updated_at: 'after', draft_id: 'draft-1', platform: 'ozon', site: 'global', category_id: 'cat-1',
    changed_keys: ['85'], attributes: { '85': { values: [{ dictionary_value_id: 'no-brand', value: 'Нет бренда' }] } }, ...overrides,
  } }
}
function setup() {
  const chat = reactive({ activeConversationId: 'conversation-1', messages: [{ id: 'user-1', role: 'user', parts: [] as unknown[] }] })
  state.chat = chat
  const draft = ref(createEmptyDraftDetail('ozon'))
  draft.value.draftId = 'draft-1'
  draft.value.site = 'global'
  draft.value.updatedAt = 'before'
  draft.value.targetSites = [{ platform: 'ozon', site: 'global', categoryId: 'cat-1', language: 'ru-RU', listingCurrency: 'RUB', attributes: { manual: '保留' } }]
  draft.value.skuItems = [{ sku_id: 's0', sku: 's0', selected: true, stock: '', overrides: {}, pricing: {}, publications: {}, attributes_by_target: {} }]
  const scope = effectScope()
  scope.run(() => useAiAttributeResults(draft))
  return { chat, draft, scope }
}

describe('主对话属性保存结果展示', () => {
  beforeEach(() => { state.chat = null })
  it('本轮保存按字段显示，保留其他手工输入，历史重放不会重复覆盖', async () => {
    const { chat, draft, scope } = setup()
    chat.messages.push({ id: 'assistant-1', role: 'assistant', parts: [receipt()] })
    await nextTick()
    expect(draft.value.targetSites[0]!.attributes).toEqual({ manual: '保留', '85': { values: [{ dictionaryValueId: 'no-brand', value: 'Нет бренда' }] } })
    expect(draft.value.updatedAt).toBe('after')
    draft.value.targetSites[0]!.attributes!['85'] = '之后手工修改'
    chat.messages = structuredClone(JSON.parse(JSON.stringify(chat.messages)))
    await nextTick()
    expect(draft.value.targetSites[0]!.attributes!['85']).toBe('之后手工修改')
    chat.activeConversationId = 'history'
    chat.messages = [{ id: 'older-user', role: 'user', parts: [] }, { id: 'old-answer', role: 'assistant', parts: [receipt()] }]
    await nextTick()
    expect(draft.value.targetSites[0]!.attributes!['85']).toBe('之后手工修改')
    scope.stop()
  })
  it.each([{ ok: false }, { draft_id: 'other' }, { category_id: 'old-category' }])('失败或目标变化后的回执不覆盖当前表单 %j', async overrides => {
    const { chat, draft, scope } = setup()
    chat.messages.push({ id: 'assistant', role: 'assistant', parts: [receipt(overrides)] })
    await nextTick()
    expect(draft.value.targetSites[0]!.attributes).toEqual({ manual: '保留' })
    scope.stop()
  })
  it('SKU 保存仅更新指定 SKU，不更改公共属性', async () => {
    const { chat, draft, scope } = setup()
    chat.messages.push({ id: 'assistant', role: 'assistant', parts: [{ ...receipt({ sku_id: 's0', changed_keys: ['color'], attributes: { color: 'Черный' } }), type: 'tool-draft_sku_attributes_update' }] })
    await nextTick()
    expect(draft.value.skuItems[0]!.attributes_by_target['ozon:global']).toEqual({ color: 'Черный' })
    expect(draft.value.targetSites[0]!.attributes).toEqual({ manual: '保留' })
    scope.stop()
  })
})
