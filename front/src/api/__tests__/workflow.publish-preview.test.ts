import { describe, expect, it, vi } from 'vitest'
import { apiClient } from '@/api/client'
import { previewPublishPayload } from '@/api/workflow/publishing'
import { createEmptyDraftDetail } from '@/constants/initialState'

vi.mock('@/api/client', () => ({ API_REQUEST_TIMEOUT_MS: 180_000, apiClient: { post: vi.fn() } }))

describe('发布素材准备与预览', () => {
  it('等待实际准备结果，不因通用三分钟超时丢失回执', async () => {
    const draft = createEmptyDraftDetail('yandex')
    draft.draftId = 'draft-1'
    vi.mocked(apiClient.post).mockResolvedValueOnce({ data: {
      ok: true, platform: 'yandex', site: 'global', status: 'preview_only',
      validation_digest: 'confirmed-digest', payload: {}, summary: {}, warnings: [],
    } })
    const result = await previewPublishPayload(draft, {
      platform: 'yandex', site: 'global', language: 'ru', listingCurrency: 'RUB',
    })
    expect(apiClient.post).toHaveBeenCalledWith('/api/publish-payload-preview',
      expect.objectContaining({ draft_id: 'draft-1', platform: 'yandex', site: 'global' }),
      { timeout: 0 })
    expect(result.validationDigest).toBe('confirmed-digest')
  })
})

it('预览请求完成前接收真实数量，分段与合并回执均不丢失、不重复', async () => {
  const draft = createEmptyDraftDetail('yandex')
  draft.draftId = 'draft-stream'
  const reports: unknown[] = []
  const first = JSON.stringify({ type: 'progress', stage: 'uploading', completed: 0, total: 2 }) + '\n'
  const second = JSON.stringify({ type: 'progress', stage: 'uploading', completed: 1, total: 2 }) + '\n'
  const stage = JSON.stringify({ type: 'progress', stage: 'validating_final' }) + '\n'
  const final = JSON.stringify({ type: 'result', status: 200, data: {
    ok: true, platform: 'yandex', site: 'global', validation_digest: 'stream-confirmed',
  } }) + '\n'
  vi.mocked(apiClient.post).mockImplementationOnce(async (_url, _body, config) => {
    expect(config).toMatchObject({ timeout: 0, adapter: 'xhr', responseType: 'text', headers: { Accept: 'application/x-ndjson' } })
    const emit = (text: string) => config!.onDownloadProgress!({ event: { target: { responseText: text } } } as never)
    emit(first.slice(0, 15))
    expect(reports).toHaveLength(0)
    emit(first + second.slice(0, 10))
    expect(reports).toEqual([{ stage: 'uploading', completed: 0, total: 2 }])
    emit(first + second + stage)
    emit(first + second + stage)
    expect(reports).toHaveLength(3)
    return { data: first + second + stage + final }
  })
  const result = await previewPublishPayload(draft, { platform: 'yandex', site: 'global', language: 'ru', listingCurrency: 'RUB' }, (value) => reports.push(value))
  expect(result.validationDigest).toBe('stream-confirmed')
  expect(reports[1]).toEqual({ stage: 'uploading', completed: 1, total: 2 })
})

it.each([
  ['缺少最终回执', '{"type":"progress","stage":"uploading","completed":1,"total":2}\n', '未收到准备结果'],
  ['失败回执', '{"type":"result","status":400,"data":{"ok":false,"error":"图片上传失败"}}\n', '图片上传失败'],
  ['损坏回执', 'bad-json\n', '进度格式无效'],
])('%s 不产生可确认指纹', async (_label, text, error) => {
  const draft = createEmptyDraftDetail('yandex')
  draft.draftId = 'draft-stream'
  vi.mocked(apiClient.post).mockResolvedValueOnce({ data: text })
  await expect(previewPublishPayload(draft, { platform: 'yandex', site: 'global', language: 'ru', listingCurrency: 'RUB' }, vi.fn())).rejects.toThrow(error)
})
