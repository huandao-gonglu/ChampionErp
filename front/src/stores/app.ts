import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { detectUiLocale, setUiLocale as applyUiLocale } from '@/i18n'
import type { SupportedUiLocale } from '@/constants/locales'
import type { UnknownRecord } from '@/types/workflow'

export type ToastKind = 'success' | 'info' | 'warning' | 'error'

export interface ToastMessage {
  id: number
  kind: ToastKind
  message: string
}

function preferredDarkMode(): boolean {
  const saved = localStorage.getItem('theme')
  if (saved === 'dark') return true
  if (saved === 'light') return false
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? false
}

export const useAppStore = defineStore('app', () => {
  const sidebarCollapsed = ref(localStorage.getItem('sidebarCollapsed') === '1')
  const mobileSidebarOpen = ref(false)
  const globalLoading = ref(false)
  const darkMode = ref(preferredDarkMode())
  const uiLocale = ref<SupportedUiLocale>(detectUiLocale())
  const publicConfig = ref<UnknownRecord>({})
  const toasts = ref<ToastMessage[]>([])
  let toastId = 0

  const theme = computed(() => (darkMode.value ? 'dark' : 'light'))

  function applyTheme() {
    document.documentElement.classList.toggle('dark', darkMode.value)
    localStorage.setItem('theme', darkMode.value ? 'dark' : 'light')
  }

  function toggleTheme() {
    darkMode.value = !darkMode.value
    applyTheme()
  }

  function setUiLocale(locale: SupportedUiLocale) {
    uiLocale.value = locale
    applyUiLocale(locale)
  }

  function setSidebarCollapsed(value: boolean) {
    sidebarCollapsed.value = value
    localStorage.setItem('sidebarCollapsed', value ? '1' : '0')
  }

  function pushToast(message: string, kind: ToastKind = 'info') {
    const toast = { id: ++toastId, kind, message }
    toasts.value.push(toast)
    window.setTimeout(() => dismissToast(toast.id), 4000)
  }

  function dismissToast(id: number) {
    toasts.value = toasts.value.filter((toast) => toast.id !== id)
  }

  return {
    sidebarCollapsed,
    mobileSidebarOpen,
    globalLoading,
    darkMode,
    uiLocale,
    publicConfig,
    toasts,
    theme,
    applyTheme,
    toggleTheme,
    setUiLocale,
    setSidebarCollapsed,
    pushToast,
    dismissToast,
  }
})
