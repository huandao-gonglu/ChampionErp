import { computed, inject, onActivated, onDeactivated, onScopeDispose, provide, ref, watch } from 'vue'
import type { ComputedRef, InjectionKey } from 'vue'
import { useAiPageContextStore } from '@/stores/aiPageContext'
import type { AiPageContext } from '@/types/aiPageContext'

const visiblePageContext: InjectionKey<ComputedRef<boolean>> = Symbol('visible-page-context')

/** 只发布当前可见页面的定位信息，子编辑器用更高优先级提供具体位置。 */
export function useAiPageContext(source: () => AiPageContext | null, priority = 0): void {
  const store = useAiPageContextStore()
  const owner = Symbol('page-context')
  const active = ref(true)
  const parentVisible = inject(visiblePageContext, computed(() => true))
  const visible = computed(() => active.value && parentVisible.value)
  provide(visiblePageContext, visible)
  watch(() => visible.value ? source() : null, (context) => {
    store.setSource(owner, context, priority)
  }, { immediate: true, deep: true, flush: 'sync' })
  onActivated(() => { active.value = true })
  onDeactivated(() => { active.value = false })
  onScopeDispose(() => store.setSource(owner, null))
}
