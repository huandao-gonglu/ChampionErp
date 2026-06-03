import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import type { UnknownRecord } from '@/types/workflow'

export interface AuthUser {
  id: string
  name: string
  role: 'user' | 'admin'
  raw?: UnknownRecord
}

export const useAuthStore = defineStore('auth', () => {
  const user = ref<AuthUser | null>(null)
  const accessToken = ref(localStorage.getItem('accessToken') || '')
  const refreshToken = ref(localStorage.getItem('refreshToken') || '')
  const initialized = ref(false)

  const isAuthenticated = computed(() => Boolean(accessToken.value || user.value))
  const isAdmin = computed(() => user.value?.role === 'admin')

  async function restore() {
    initialized.value = true
  }

  function setTokens(nextAccessToken: string, nextRefreshToken = '') {
    accessToken.value = nextAccessToken
    refreshToken.value = nextRefreshToken
    if (nextAccessToken) localStorage.setItem('accessToken', nextAccessToken)
    else localStorage.removeItem('accessToken')
    if (nextRefreshToken) localStorage.setItem('refreshToken', nextRefreshToken)
    else localStorage.removeItem('refreshToken')
  }

  function logout() {
    user.value = null
    setTokens('', '')
  }

  return {
    user,
    accessToken,
    refreshToken,
    initialized,
    isAuthenticated,
    isAdmin,
    restore,
    setTokens,
    logout,
  }
})
