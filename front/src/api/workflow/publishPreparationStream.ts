import { apiClient } from '@/api/client'
import type { BackendPublishPreparationProgress, UnknownRecord } from '@/types/workflow'

const stages = new Set<BackendPublishPreparationProgress['stage']>([
  'validating_source', 'uploading', 'saving_images', 'validating_final', 'building_preview', 'saving_preview',
])

/** 图片由服务端上传；此处读取服务端逐张回执，不能使用浏览器请求体上传进度。 */
export async function preparePublishWithProgress(
  body: UnknownRecord,
  onProgress: (progress: BackendPublishPreparationProgress) => void,
): Promise<UnknownRecord> {
  let consumed = 0
  let result: UnknownRecord | null = null
  let parseError: Error | null = null
  function consume(text: string, finished = false) {
    if (parseError) return
    try {
      const end = finished ? text.length : text.lastIndexOf('\n') + 1
      if (end <= consumed) return
      const lines = text.slice(consumed, end).split('\n').filter((line) => line.trim())
      consumed = end
      for (const line of lines) {
        const event = JSON.parse(line)
        if (result) throw new Error('发布准备回执重复，请重新准备。')
        if (event.type === 'result' && event.data && typeof event.data === 'object') {
          result = event.data
        } else if (event.type === 'progress' && stages.has(event.stage)) {
          const progress: BackendPublishPreparationProgress = { stage: event.stage }
          if (event.total !== undefined || event.completed !== undefined) {
            if (!Number.isInteger(event.total) || !Number.isInteger(event.completed)
              || event.completed < 0 || event.total < event.completed) throw new Error('图片准备进度无效。')
            progress.completed = event.completed
            progress.total = event.total
          }
          onProgress(progress)
        } else {
          throw new Error('无法读取发布准备进度，请重新准备。')
        }
      }
    } catch (error) {
      parseError = error instanceof SyntaxError ? new Error('发布准备进度格式无效，请重新准备。')
        : error instanceof Error ? error : new Error('无法读取发布准备进度。')
    }
  }
  const response = await apiClient.post<string>('/api/publish-payload-preview', body, {
    timeout: 0,
    adapter: 'xhr',
    responseType: 'text',
    headers: { Accept: 'application/x-ndjson' },
    onDownloadProgress: ({ event }) => {
      const text = event?.target?.responseText
      if (typeof text === 'string') consume(text)
    },
  })
  consume(response.data, true)
  if (parseError) throw parseError
  if (!result) throw new Error('连接已结束，但未收到准备结果。请重新准备以核对已上传图片。')
  return result
}
