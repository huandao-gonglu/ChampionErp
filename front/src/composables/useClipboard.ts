import { ref } from 'vue'

export function useClipboard() {
  const copied = ref(false)

  async function copy(text: string) {
    await navigator.clipboard?.writeText(text)
    copied.value = true
    window.setTimeout(() => {
      copied.value = false
    }, 1500)
  }

  return { copied, copy }
}
