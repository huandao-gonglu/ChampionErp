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
  it('脚本汇总或报错后按实际写回执更新字段和保存版本，重放不覆盖手工输入', async () => {
    const { chat, draft, scope } = setup()
    const first = receipt({ sku_id: 's0', changed_keys: ['color'], attributes: { color: 'EE047' } })
    const second = receipt({ previous_updated_at: 'after', updated_at: 'final' })
    chat.messages.push({
      id: 'script', role: 'assistant',
      parts: [{ type: 'tool-run_code', toolCallId: 'script', state: 'output-error', errorText: '后续操作失败' }],
      metadata: { business_write_receipts: [
        { tool_call_id: 'script__1', tool_name: 'draft_sku_attributes_update', output: first.output },
        { tool_call_id: 'script__2', tool_name: 'product_attributes_update', output: second.output },
      ] },
    } as typeof chat.messages[number])
    await nextTick()
    expect(draft.value.skuItems[0]!.attributes_by_target['ozon:global']).toEqual({ color: 'EE047' })
    expect(draft.value.targetSites[0]!.attributes!.manual).toBe('保留')
    expect(draft.value.updatedAt).toBe('final')
    draft.value.skuItems[0]!.attributes_by_target['ozon:global']!.color = '手工修改'
    chat.messages = JSON.parse(JSON.stringify(chat.messages))
    await nextTick()
    expect(draft.value.skuItems[0]!.attributes_by_target['ozon:global']!.color).toBe('手工修改')
    scope.stop()
  })
  it('成组回执同步属性、包装、库存和一次保存版本，保留未提交字段', async () => {
    const { chat, draft, scope } = setup()
    draft.value.skuItems[0]!.overrides = { package_dimensions: { weight_kg: '2.5' }, cost_cny: '17' }
    draft.value.skuItems[0]!.attributes_by_target['ozon:global'] = { remove: '清除', manual: '手工输入' }
    chat.messages.push({ id: 'batch', role: 'assistant', parts: [{
      type: 'tool-draft_changes_apply', toolCallId: 'batch', state: 'output-available', output: {
        draft_id: 'draft-1', platform: 'ozon', site: 'global', category_id: 'cat-1',
        changed: true, previous_updated_at: 'before', updated_at: 'after', changes: [
          { sku_id: 's0', changed: true, attributes: { color: 'EE047' }, changed_keys: ['color', 'remove'], package_dimensions: { length_cm: '12.0' }, stock: '0' },
          { sku_id: '', changed: true, attributes: { '85': '品牌' }, changed_keys: ['85'] },
        ],
      },
    }] })
    await nextTick()
    expect(draft.value.skuItems[0]!.attributes_by_target['ozon:global']).toEqual({ color: 'EE047', manual: '手工输入' })
    expect(draft.value.skuItems[0]!.overrides).toEqual({ cost_cny: '17', package_dimensions: { length_cm: '12.0', weight_kg: '2.5' } })
    expect(draft.value.skuItems[0]!.stock).toBe('0')
    expect(draft.value.targetSites[0]!.attributes).toEqual({ manual: '保留', '85': '品牌' })
    expect(draft.value.updatedAt).toBe('after')
    draft.value.skuItems[0]!.stock = '手工库存'
    chat.messages = JSON.parse(JSON.stringify(chat.messages))
    await nextTick()
    expect(draft.value.skuItems[0]!.stock).toBe('手工库存')
    scope.stop()
  })
})
