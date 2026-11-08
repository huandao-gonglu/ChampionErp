import { apiClient } from './client'
import type { BackendUserAgentsDocument } from '@/types/workflow.generated'

export async function fetchUserAgents(): Promise<BackendUserAgentsDocument> {
  return (await apiClient.get<BackendUserAgentsDocument>('/api/system-settings/user-agents')).data
}

export async function saveUserAgents(content: string, expectedRevision: string): Promise<BackendUserAgentsDocument> {
  return (await apiClient.post<BackendUserAgentsDocument>('/api/system-settings/user-agents', {
    content, expected_revision: expectedRevision,
  })).data
}
