import axios, { AxiosError } from 'axios'
import { uiLocaleOption } from '@/constants/locales'
import { normalizeApiError } from '@/utils/apiError'

/**
 * 仅传输层使用的请求 option：request interceptor 将其转换为
 * `X-AI-Presentation-ID` header，用于把业务请求关联到已预留的 AI 展示。
 * 该字段只存在于 axios config，不进入业务 JSON、日志或持久化。
 */
declare module 'axios' {
  export interface AxiosRequestConfig {
    aiPresentationId?: string
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
  config.headers.set('Accept-Language', uiLocaleOption(localStorage.getItem('uiLocale') || localStorage.getItem('locale')).acceptLanguage)
  config.headers.set('X-Timezone', Intl.DateTimeFormat().resolvedOptions().timeZone)
  if (config.aiPresentationId) {
    config.headers.set('X-AI-Presentation-ID', config.aiPresentationId)
  }
  return config
})

apiClient.interceptors.response.use(
  (response) => response,
  (error: AxiosError) => {
    const data = error.response?.data as Record<string, unknown> | undefined
    const normalized = normalizeApiError(
      {
        message: data?.error || data?.message || error.message,
        code: data?.code || data?.error_code,
        status: error.response?.status,
        raw: data || error,
      },
      '网络请求失败',
    )
    return Promise.reject(Object.assign(new Error(normalized.message), normalized))
  },
)
