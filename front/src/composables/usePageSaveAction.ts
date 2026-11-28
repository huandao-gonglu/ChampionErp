import {
  computed,
  inject,
  onBeforeUnmount,
  provide,
  shallowRef,
  toValue,
  watchEffect,
  type ComputedRef,
  type InjectionKey,
  type MaybeRefOrGetter,
} from 'vue'

export interface PageSaveAction {
  label: string
  disabled?: boolean
  title?: string
  testId?: string
  form?: string
  submitForm?: () => void
  onClick?: () => void
}

interface PageSaveActionRegistry {
  setAction: (owner: symbol, action: PageSaveAction | null) => void
}

const PAGE_SAVE_ACTION_KEY: InjectionKey<PageSaveActionRegistry> = Symbol('page-save-action')

export function providePageSaveAction(): {
  action: ComputedRef<PageSaveAction | null>
} {
  const entries = shallowRef<Array<{ owner: symbol; action: PageSaveAction }>>([])

  function setAction(owner: symbol, next: PageSaveAction | null) {
    const rest = entries.value.filter(entry => entry.owner !== owner)
    entries.value = next ? [...rest, { owner, action: next }] : rest
  }

  provide(PAGE_SAVE_ACTION_KEY, { setAction })

  const action = computed(() => entries.value.at(-1)?.action ?? null)
  return { action }
}

export function usePageSaveAction(
  getter: MaybeRefOrGetter<PageSaveAction | null>,
): { hasHost: boolean } {
  const registry = inject(PAGE_SAVE_ACTION_KEY, null)
  if (!registry) return { hasHost: false }

  const owner = Symbol('page-save-owner')
  watchEffect(() => {
    registry.setAction(owner, toValue(getter))
  })
  onBeforeUnmount(() => {
    registry.setAction(owner, null)
  })
  return { hasHost: true }
}
