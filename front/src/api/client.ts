import { hasLocalOperations, OPERATION_AVAILABILITY_CHANGED } from '@/utils/desktop'
import axios, { AxiosError } from 'axios'
import { normalizeApiError } from '@/utils/apiError'
import { accountHeaders, sessionExpired } from '@/utils/accountTransport'

/** 手动 AI 操作与新建普通对话的关联。 */
declare module 'axios' {
  export interface AxiosRequestConfig {
    aiConversationId?: string
  }
}

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || ''
export const API_REQUEST_TIMEOUT_MS = 180_000

export const apiClient = axios.create({
  baseURL: API_BASE_URL,
  withCredentials: true,
  timeout: API_REQUEST_TIMEOUT_MS,
  headers: {
    'Content-Type': 'application/json',
  },
})

apiClient.interceptors.request.use((config) => {
  if (!['get', 'head', 'options'].includes(config.method || 'get')) {
    for (const [name, value] of Object.entries(accountHeaders())) config.headers.set(name, value)
  }
  if (hasLocalOperations()) config.baseURL = ''
  config.headers.set('Accept-Language', 'zh-CN,zh;q=0.9')
  config.headers.set('X-Timezone', Intl.DateTimeFormat().resolvedOptions().timeZone)
  if (config.aiConversationId) {
    config.headers.set('X-AI-Conversation-ID', config.aiConversationId)
  }
  return config
})

function observePlatformRejection(path?: string, code?: unknown) {
  if (path !== '/api/operation-availability' && (code === 'OPERATION_UNAVAILABLE' || code === 'DESKTOP_UNAVAILABLE')) {
    window.dispatchEvent(new Event(OPERATION_AVAILABILITY_CHANGED))
  }
}

apiClient.interceptors.response.use(
  (response) => {
    observePlatformRejection(response.config.url, response.data?.error_code)
    return response
  },
  (error: AxiosError) => {
    const publicAccount = ['/api/account/login', '/api/account/register', '/api/account/session'].includes(error.config?.url || '')
    if (error.response?.status === 401 && !publicAccount) sessionExpired()
    const data = error.response?.data as Record<string, unknown> | undefined
    observePlatformRejection(error.config?.url, data?.error_code)
    const normalized = normalizeApiError(
      {
        message: data?.error || data?.message || error.message,
        code: data?.code || data?.error_code,
        status: error.response?.status,
      },
      '网络请求失败',
    )
    return Promise.reject(Object.assign(new Error(normalized.message), normalized, { raw: data || error }))
  },
)
