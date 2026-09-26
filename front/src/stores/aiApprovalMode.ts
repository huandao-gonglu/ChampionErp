import { defineStore } from 'pinia'
import { ref } from 'vue'
import { fetchAiApprovalMode, saveAiApprovalMode } from '@/api/aiApprovalMode'
import type { AiToolApprovalMode } from '@/api/aiApprovalMode'

export const useAiApprovalModeStore = defineStore('aiApprovalMode', () => {
  const mode = ref<AiToolApprovalMode | null>(null)
  const busy = ref(false)
  const error = ref('')

  async function refresh(): Promise<void> {
    if (busy.value) return
    busy.value = true
    error.value = ''
    try { mode.value = await fetchAiApprovalMode() }
    catch (cause) { error.value = cause instanceof Error ? cause.message : '读取审批模式失败，请重试' }
    finally { busy.value = false }
  }

  async function setMode(value: AiToolApprovalMode): Promise<boolean> {
    if (busy.value) return false
    busy.value = true
    error.value = ''
    try {
      mode.value = await saveAiApprovalMode(value)
      return true
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : '审批模式未保存，请重试'
      return false
    } finally { busy.value = false }
  }

  return { mode, busy, error, refresh, setMode }
})
