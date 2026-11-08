import type { Directive } from 'vue'
import { hasLocalOperations, modelRequiresDesktop } from './desktop'
import { launchDesktop } from './desktopHandoff'

type DesktopAction = string | boolean
const listeners = new WeakMap<HTMLElement, { value: DesktopAction; listener: EventListener }>()

/** 在原按钮处理前接管网页动作，按钮自身的加载与必填约束继续生效。 */
export const vDesktop: Directive<HTMLElement, DesktopAction> = {
  mounted(element, binding) {
    const entry = { value: binding.value, listener: (() => {}) as EventListener }
    entry.listener = event => {
      if (element.tagName === 'FORM' && event.type === 'click') return
      const required = typeof entry.value === 'string' ? modelRequiresDesktop(entry.value) : entry.value && !hasLocalOperations()
      if (!required || (element as HTMLButtonElement).disabled) return
      event.preventDefault()
      event.stopImmediatePropagation()
      void launchDesktop()
    }
    listeners.set(element, entry)
    element.addEventListener('click', entry.listener, true)
    element.addEventListener('submit', entry.listener, true)
  },
  updated(element, binding) {
    const entry = listeners.get(element)
    if (entry) entry.value = binding.value
  },
  unmounted(element) {
    const entry = listeners.get(element)
    if (entry) {
      element.removeEventListener('click', entry.listener, true)
      element.removeEventListener('submit', entry.listener, true)
      listeners.delete(element)
    }
  },
}
