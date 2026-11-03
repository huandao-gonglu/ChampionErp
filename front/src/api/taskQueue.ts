import { apiClient } from './client'
import type { BackendTaskQueueDetail, BackendTaskQueueResponse } from '@/types/workflow.generated'

export async function fetchTasks(params: Record<string, string | number>) {
  const { data } = await apiClient.get<BackendTaskQueueResponse>('/api/tasks', { params })
  return data
}

export async function fetchTask(id: string) {
  const { data } = await apiClient.get<BackendTaskQueueDetail>('/api/tasks', { params: { id } })
  return data
}

export async function checkTask(task_id: string) {
  const { data } = await apiClient.post<BackendTaskQueueDetail & { checked: boolean; resolution: string }>('/api/tasks/check', { task_id })
  return data
}

export async function retryPublishTask(job_id: string, platform: string) {
  await apiClient.post('/api/publish-bus/retry', { job_id, platform })
}
