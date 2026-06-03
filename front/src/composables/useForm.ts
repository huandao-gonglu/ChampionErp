import { reactive } from 'vue'

export function useForm<T extends object>(initialValue: T) {
  const form = reactive({ ...initialValue }) as T

  function reset(nextValue: T = initialValue) {
    Object.assign(form, nextValue)
  }

  return { form, reset }
}
