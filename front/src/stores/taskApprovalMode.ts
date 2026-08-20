import { ref } from 'vue'
import { defineStore } from 'pinia'
import {
  fetchTaskApprovalMode,
  saveTaskApprovalMode,
  type TaskApprovalMode,
} from '@/api/taskApprovalMode'

export const useTaskApprovalModeStore = defineStore('taskApprovalMode', () => {
  const mode = ref<TaskApprovalMode>('ask')
  const loaded = ref(false)
  const busy = ref(false)
  const error = ref('')
  let loadingPromise: Promise<void> | null = null

  async function ensureLoaded(): Promise<void> {
    if (loaded.value) return
    if (loadingPromise) return loadingPromise
    loadingPromise = (async () => {
      busy.value = true
      error.value = ''
      try {
        mode.value = await fetchTaskApprovalMode()
        loaded.value = true
      } catch (cause) {
        error.value = cause instanceof Error
          ? cause.message
          : '读取访问权限失败。'
      } finally {
        busy.value = false
        loadingPromise = null
      }
    })()
    return loadingPromise
  }

  async function setMode(nextMode: TaskApprovalMode): Promise<boolean> {
    if (busy.value || nextMode === mode.value) return true
    busy.value = true
    error.value = ''
    try {
      mode.value = await saveTaskApprovalMode(nextMode)
      loaded.value = true
      return true
    } catch (cause) {
      error.value = cause instanceof Error
        ? cause.message
        : '保存访问权限失败。'
      return false
    } finally {
      busy.value = false
    }
  }

  return { mode, loaded, busy, error, ensureLoaded, setMode }
})

