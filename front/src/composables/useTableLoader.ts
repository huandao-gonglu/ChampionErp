import { ref } from 'vue'

export function useTableLoader<T>(loader: () => Promise<T[]>) {
  const rows = ref<T[]>([])
  const loading = ref(false)
  const error = ref('')

  async function load() {
    loading.value = true
    error.value = ''
    try {
      rows.value = await loader()
    } catch (exc) {
      error.value = exc instanceof Error ? exc.message : '加载失败'
    } finally {
      loading.value = false
    }
  }

  return { rows, loading, error, load }
}
