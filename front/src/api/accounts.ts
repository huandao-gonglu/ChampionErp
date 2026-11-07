import { apiClient } from './client'

export interface AccountUser {
  id: string
  email: string
  display_name: string
  role: 'admin' | 'user'
  status: 'enabled' | 'disabled'
  created_at: number
  updated_at: number
}
export interface AccountSessionResponse {
  ok: boolean
  user: AccountUser | null
  csrf_token: string
  initialized?: boolean
}
export interface CreateAccount { email: string; display_name: string; password: string }
export const fetchAccountSession = async () => (await apiClient.get<AccountSessionResponse>('/api/account/session')).data
export const loginAccount = async (body: { email: string; password: string }) => (await apiClient.post<AccountSessionResponse>('/api/account/login', body)).data
export const registrationConfig = async () => (await apiClient.get<{ site_key: string }>('/api/account/registration-config')).data
export const sendRegistrationCode = (email: string, token: string) =>
  apiClient.post('/api/account/registration-code', { email, human_token: token })
export const registerAccount = async (body: CreateAccount & { email_code: string }) => (await apiClient.post('/api/account/register', body)).data
export const logoutAccount = () => apiClient.post('/api/account/logout', {})
export const changeAccountPassword = (body: { current_password: string; password: string }) => apiClient.post('/api/account/password', body)
export const listAccountUsers = async (params: { page: number; q: string }) => (await apiClient.get<{ items: AccountUser[]; total: number; page: number; limit: number }>('/api/admin/users', { params })).data
export const createAccountUser = (body: CreateAccount) => apiClient.post('/api/admin/users', body)
export const setAccountUserStatus = (user_id: string, status: AccountUser['status']) => apiClient.post('/api/admin/users/status', { user_id, status })
export const resetAccountUserPassword = (user_id: string, password: string) => apiClient.post('/api/admin/users/password', { user_id, password })

export const fetchNewAPIInfo = async () => (await apiClient.get<{ configured: boolean; url: string; sync_pending: boolean }>('/api/account/newapi')).data
export const previewNewAPI = async (code: string) => (await apiClient.post<{ code: string; target: 'keys' | 'wallet' | 'usage' | 'proof'; expires_at: number }>('/api/account/newapi/preview', { code })).data
export const authorizeNewAPI = (body: { code: string; password?: string }) => apiClient.post('/api/account/newapi/authorize', body)

export interface NewAPILaunch { ok: boolean; url: string; ticket: string; expires_at: number }
export const launchNewAPI = async (target: 'keys' | 'wallet' | 'usage') => (await apiClient.post<NewAPILaunch>('/api/account/newapi/launch', { target })).data
