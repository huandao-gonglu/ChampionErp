import { onBeforeUnmount } from 'vue'

export function useAutoRefresh(callback: () => void | Promise<void>, intervalMs: number) {
  const timer = window.setInterval(() => void callback(), intervalMs)
  onBeforeUnmount(() => window.clearInterval(timer))
  return () => window.clearInterval(timer)
}
