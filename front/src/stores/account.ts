import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { fetchAccountSession, loginAccount, logoutAccount, type AccountUser } from '@/api/accounts'
import { accountDestination, clearBusinessStorage, setCsrfToken, setAccountIdentity } from '@/utils/accountTransport'

export const useAccountStore = defineStore('account', () => {
  const user = ref<AccountUser | null>(null)
  const ready = ref(false)
  const initialized = ref(true)
  const error = ref('')
  const isAdmin = computed(() => user.value?.role === 'admin')
  let loading: Promise<void> | null = null

  function refresh() {
    if (loading) return loading
    loading = (async () => {
      try {
        const data = await fetchAccountSession()
        user.value = data.user
        setAccountIdentity(data.user?.id || '')
        setCsrfToken(data.csrf_token)
        initialized.value = data.initialized !== false
        error.value = ''
        ready.value = true
      } catch (cause) {
        error.value = cause instanceof Error ? cause.message : String(cause)
        throw cause
      } finally { loading = null }
    })()
    return loading
  }

  function reloadIdentity(destination: string) {
    clearBusinessStorage()
    try {
      const channel = new BroadcastChannel('champion-account')
      channel.postMessage('changed')
      channel.close()
    } catch { /* 当前标签仍会完整清理。 */ }
    window.location.replace(destination)
  }

  async function login(email: string, password: string, destination: unknown) {
    const data = await loginAccount({ email, password })
    user.value = data.user
    setCsrfToken(data.csrf_token)
    reloadIdentity(accountDestination(destination))
  }

  function signedOut() {
    user.value = null
    setAccountIdentity('')
    setCsrfToken('')
    clearBusinessStorage()
    // 清除 KeepAlive、Pinia、进行中的订阅和页面缓存，不向领域任务发送取消。
    window.location.replace('/login')
  }

  async function logout() {
    const result = await logoutAccount()
    reloadIdentity(result?.data?.newapi_sync_pending ? '/login?newapi_sync=pending' : '/login')
  }

  return { user, ready, initialized, error, isAdmin, refresh, login, logout, signedOut, reloadIdentity }
})
