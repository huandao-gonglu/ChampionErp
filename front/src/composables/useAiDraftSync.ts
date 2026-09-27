import { computed, nextTick, onScopeDispose, ref, watch, type Ref } from 'vue'
import { loadDraft } from '@/api/workflow/catalog'
import { toBackendDraftDetail, type DraftMutationResponse } from '@/api/workflow/normalizers'
import { draftPricingPayload } from '@/api/workflow/publishing'
import { useAiChatStore } from '@/stores/aiChat'
import type { DraftDetail, PricingInput } from '@/types/workflow'

// 结果与校验状态不属于用户输入；预览、切换市场不能被误认成未保存的核价设置。
const resultFields = new Set(['raw', 'updated_at', 'expected_updated_at', 'pricing', 'status',
  'publish_status', 'last_precheck', 'last_precheck_target', 'category_precheck', 'validation_errors',
  'publication', 'publications', 'currency_fingerprint', 'listing_currency'])

function signature(value: unknown) {
  return JSON.stringify(value,
    (key, value) => resultFields.has(key) ? undefined : value)
}

/** 对话提交后读取业务事实；不从模型文本推断写入，也不重放业务操作。 */
export function useAiDraftSync(options: {
  draft: Ref<DraftDetail>
  input: Ref<PricingInput>
  loading: Ref<boolean>
  accept: (result: DraftMutationResponse) => void
  clearError: () => void
}) {
  const { draft, input, loading, accept, clearError } = options
  const chat = useAiChatStore()
  const draftSignature = () => signature(toBackendDraftDetail(draft.value))
  const pricingSignature = () => signature(draftPricingPayload(draft.value, input.value))
  const baseline = ref(draftSignature())
  const pricingBaseline = ref(pricingSignature())
  const pending = ref(false)
  const refreshing = ref(false)
  const refreshError = ref('')
  const dirty = computed(() => baseline.value !== draftSignature() || pricingBaseline.value !== pricingSignature())
  let disposed = false
  let queued = false
  let generation = 0

  // API 加载/保存返回新草稿对象；等同一轮派生表单回填完再建立比较基线。
  watch(draft, async (value, previous) => {
    const token = ++generation
    const pricingChanged = !previous || previous.draftId !== value.draftId
      || JSON.stringify(previous.pricing) !== JSON.stringify(value.pricing)
    pending.value = false
    refreshError.value = ''
    await nextTick()
    if (!disposed && token === generation) {
      baseline.value = draftSignature()
      // 保存文本不等于应用核价，不能把仍未保存的利润设置标记为已保存。
      if (pricingChanged) pricingBaseline.value = pricingSignature()
    }
  }, { immediate: true, flush: 'sync' })

  async function refresh(discardEdits = false) {
    if (disposed || !draft.value.draftId) return false
    if (loading.value || refreshing.value) { queued = true; return false }
    queued = false
    const current = draft.value
    const version = current.updatedAt
    const token = generation
    const requestedEdits = [draftSignature(), pricingSignature()].join('\n')
    refreshing.value = true
    refreshError.value = ''
    try {
      const result = await loadDraft(current.draftId)
      // 换了草稿、保存了新版本或销毁页面后，旧读取响应不得覆盖当前状态。
      if (disposed || token !== generation || draft.value !== current || draft.value.updatedAt !== version) return false
      if (loading.value) { queued = true; return false }
      if (!discardEdits && result.draft.updatedAt === version) { pending.value = false; return true }
      if ((!discardEdits && dirty.value) || (discardEdits && [draftSignature(), pricingSignature()].join('\n') !== requestedEdits)) {
        pending.value = true
        return false
      }
      // 系统回填期间沿用页面的 loading 边界，避免表单 watcher 使新售价失效。
      loading.value = true
      try {
        // 先让子组件接收到 loading，再改共享表单对象；否则同步 watcher 仍看到旧的 false。
        await nextTick()
        if (disposed || token !== generation || draft.value !== current || draft.value.updatedAt !== version) return false
        if ([draftSignature(), pricingSignature()].join('\n') !== requestedEdits) { pending.value = true; return false }
        accept(result)
        clearError()
        await nextTick()
        baseline.value = draftSignature()
        pricingBaseline.value = pricingSignature()
        pending.value = false
      } finally { loading.value = false }
      return true
    } catch (cause) {
      if (!disposed && token === generation) {
        refreshError.value = cause instanceof Error ? cause.message : '读取最新草稿失败，请重试。'
      }
      return false
    } finally {
      refreshing.value = false
      if (queued && !loading.value && !disposed) void refresh()
    }
  }

  // 原生历史提交同时覆盖直接工具、run_code 写入及部分成功后失败的回合。
  watch(() => [chat.activeConversationId, chat.historyVersion] as const, () => { void refresh() })
  watch(() => chat.isBusy, (busy, wasBusy) => { if (wasBusy && !busy) void refresh() })
  watch(loading, busy => { if (!busy && queued) void refresh() })
  onScopeDispose(() => { disposed = true; generation++ })

  const message = computed(() => refreshError.value
    ? `读取最新草稿失败：${refreshError.value}`
    : pending.value ? '草稿已有更新。当前页面有未保存的修改，已保留；重新加载将以最新保存的数据替换页面内容。' : '')

  return { dirty, pending, refreshing, message, refresh }
}
