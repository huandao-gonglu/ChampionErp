import { ref } from 'vue'
import { defineStore } from 'pinia'
import { useAppStore } from '@/stores/app'

export const useWorkflowActivityStore = defineStore('workflow-activity', () => {
  const logs = ref<string[]>(['等待读取后端状态。'])
  const loading = ref(false)
  const error = ref('')

  function addLog(message: string) {
    logs.value.unshift(`${new Date().toLocaleTimeString()} ${message}`)
  }

  function setError(message: string) {
    error.value = message
    if (message) {
      addLog(`错误：${message}`)
      useAppStore().pushToast(message, 'error')
    }
  }

  return {
    logs,
    loading,
    error,
    addLog,
    setError,
  }
})
