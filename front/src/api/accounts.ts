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
export const registerAccount = async (body: CreateAccount) => (await apiClient.post('/api/account/register', body)).data
export const logoutAccount = () => apiClient.post('/api/account/logout', {})
export const changeAccountPassword = (body: { current_password: string; password: string }) => apiClient.post('/api/account/password', body)
export const listAccountUsers = async (params: { page: number; q: string }) => (await apiClient.get<{ items: AccountUser[]; total: number; page: number; limit: number }>('/api/admin/users', { params })).data
export const createAccountUser = (body: CreateAccount) => apiClient.post('/api/admin/users', body)
export const setAccountUserStatus = (user_id: string, status: AccountUser['status']) => apiClient.post('/api/admin/users/status', { user_id, status })
export const resetAccountUserPassword = (user_id: string, password: string) => apiClient.post('/api/admin/users/password', { user_id, password })
