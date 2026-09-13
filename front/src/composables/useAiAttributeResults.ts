import { watch } from 'vue'
import type { Ref } from 'vue'
import { useAiChatStore } from '@/stores/aiChat'
import { normalizeAttributes, asRecord } from '@/api/workflow/normalizers'
import type { CategoryAttributeValue, DraftDetail } from '@/types/workflow'

/** 展示本轮主对话的保存回执，不触发推断、业务请求或表单保存。 */
export function useAiAttributeResults(draft: Ref<DraftDetail>) {
  const chat = useAiChatStore()
  let turn = ''
  let seen = new Set<string>()
  watch(() => [chat.activeConversationId, chat.messages] as const, ([conversation, messages]) => {
    const currentTurn = `${conversation}:${[...messages].reverse().find(message => message.role === 'user')?.id || ''}`
    const receipts = messages.flatMap(message => message.parts).filter(part => (
      (part.type === 'tool-product_attributes_update' || part.type === 'tool-draft_sku_attributes_update')
      && 'state' in part && part.state === 'output-available'
    ))
    if (currentTurn !== turn) {
      turn = currentTurn
      seen = new Set(receipts.map(part => String(asRecord(part).toolCallId)))
      return
    }
    for (const part of receipts) {
      const record = asRecord(part)
      const id = String(record.toolCallId)
      if (seen.has(id)) continue
      seen.add(id)
      const result = asRecord(record.output)
      if (result.ok === false || result.changed !== true || result.draft_id !== draft.value.draftId) continue
      const key = `${result.platform}:${result.site}`.toLowerCase()
      const target = draft.value.targetSites.find(item => `${item.platform}:${item.site}`.toLowerCase() === key)
      if (!target || target.categoryId !== result.category_id) continue
      const saved = normalizeAttributes(result.attributes)
      const apply = (attributes: Record<string, CategoryAttributeValue>) => {
        for (const field of Array.isArray(result.changed_keys) ? result.changed_keys : []) {
          if (typeof field !== 'string') continue
          if (field in saved) attributes[field] = saved[field]!
          else delete attributes[field]
        }
      }
      if (result.sku_id) {
        const row = draft.value.skuItems.find(item => item.sku_id === result.sku_id)
        if (row) apply(row.attributes_by_target[key] ||= {})
      } else {
        apply(target.attributes ||= {})
        if (`${draft.value.platform}:${draft.value.site}`.toLowerCase() === key) apply(draft.value.attributes)
      }
      target.categoryPrecheck = {}
      target.validationErrors = []
      const fields = asRecord(result.draft_fields)
      if (typeof fields.brand === 'string') draft.value.brand = fields.brand
      if (typeof fields.model === 'string') draft.value.model = fields.model
      // 只有版本连续才推进保存基线；其他工具修改过草稿时仍由原有冲突校验保护。
      if (result.previous_updated_at === draft.value.updatedAt && typeof result.updated_at === 'string' && result.updated_at) {
        draft.value.updatedAt = result.updated_at
      }
    }
  }, { immediate: true, deep: true })
}
