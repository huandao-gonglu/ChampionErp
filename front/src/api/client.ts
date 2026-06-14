import axios, { AxiosError } from 'axios'
import { uiLocaleOption } from '@/constants/locales'
import { normalizeApiError } from '@/utils/apiError'

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
  const token = localStorage.getItem('accessToken')
  if (token) config.headers.set('Authorization', `Bearer ${token}`)
  config.headers.set('Accept-Language', uiLocaleOption(localStorage.getItem('uiLocale') || localStorage.getItem('locale')).acceptLanguage)
  config.headers.set('X-Timezone', Intl.DateTimeFormat().resolvedOptions().timeZone)
  return config
})

apiClient.interceptors.response.use(
  (response) => response,
  (error: AxiosError) => {
    if (error.response?.status === 401) {
      localStorage.removeItem('accessToken')
      localStorage.removeItem('refreshToken')
    }
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
