import { apiClient } from './client'
import type { BackendDesktopHandoffCreated, BackendDesktopHandoffResolved, BackendDesktopHandoffStatus, BackendDesktopInstallers } from '@/types/workflow.generated'

export async function createDesktopHandoff(target: string, state: Record<string, unknown>) {
  return (await apiClient.post<BackendDesktopHandoffCreated>('/api/desktop-handoff/create', { target, state }, { timeout: 10000 })).data
}
export async function fetchDesktopHandoffStatus(requestId: string) {
  return (await apiClient.post<BackendDesktopHandoffStatus>('/api/desktop-handoff/status', { request_id: requestId }, { timeout: 10000 })).data
}
export async function resolveDesktopHandoff(requestId: string) {
  return (await apiClient.post<BackendDesktopHandoffResolved>('/api/desktop-handoff/resolve', { request_id: requestId }, { timeout: 10000 })).data
}
export async function confirmDesktopHandoff(requestId: string) {
  await apiClient.post('/api/desktop-handoff/acknowledge', { request_id: requestId }, { timeout: 10000 })
}
export async function fetchDesktopInstallers() {
  return (await apiClient.post<BackendDesktopInstallers>('/api/desktop-handoff/installers', {}, { timeout: 10000 })).data.items
}
export function desktopBackendOrigin() {
  return new URL(apiClient.defaults.baseURL || '/', window.location.href).origin
}
