import { apiClient } from './client'
import type { BackendImageBatchSnapshot } from '@/types/workflow.generated'

export type ImageBatchSnapshot = BackendImageBatchSnapshot

export async function fetchImageBatch(conversationId: string): Promise<ImageBatchSnapshot | null> {
  const { data } = await apiClient.get<{ ok: boolean; image_batch: ImageBatchSnapshot | null }>(
    `/api/v1/ai-work/conversations/${encodeURIComponent(conversationId)}/image-batch`,
  )
  if (!data.ok) throw new Error('读取图片进度失败')
  return data.image_batch
}
