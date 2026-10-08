import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useWorkflowPublishingStore } from '@/stores/workflow/publishing'
import { fetchPublishJob, fetchPublishJobs, reconcilePublishJob } from '@/api/workflow/publishing'
import type { PublishJobListItem } from '@/types/workflow'

vi.mock('@/api/workflow/publishing', () => ({
  fetchPublishJob: vi.fn(), fetchPublishJobs: vi.fn(), reconcilePublishJob: vi.fn(),
  fetchPublishLogs: vi.fn(),
}))

function job(id: string, status = 'pending_confirmation'): PublishJobListItem {
  return {
    jobId: id, productId: 'p', productName: '测试商品', draftId: 'd',
    status: status as PublishJobListItem['status'], rawStatus: status,
    stage: 'waiting_platform_confirmation', attempts: 1,
    error: '', errorCode: '', nextAction: '', createdAt: '', updatedAt: '',
    platforms: [{ platform: 'ozon', draftId: 'd', site: 'global', sitesToSell: [],
      status, stage: 'waiting_platform_confirmation', attempts: 1,
      error: '', errorCode: '', nextAction: '', updatedAt: '' }],
  }
}

describe('查看发布结果时的查询边界', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    vi.mocked(fetchPublishJob).mockResolvedValue({})
    vi.mocked(reconcilePublishJob).mockImplementation(async (id) => ({
      ok: true, checked: true, resolution: 'pending', summary: job(id),
    }))
  })

  it('普通刷新和加载更多只读本地记录', async () => {
    const store = useWorkflowPublishingStore()
    vi.mocked(fetchPublishJobs).mockResolvedValueOnce({ items: [job('one')], nextCursor: 'cursor' })
    vi.mocked(fetchPublishJobs).mockResolvedValueOnce({ items: [job('two')], nextCursor: '' })
    await store.refreshPublishJobs()
    await store.loadMorePublishJobs()
    expect(store.publishJobs).toHaveLength(2)
    expect(reconcilePublishJob).not.toHaveBeenCalled()
  })

  it('进入列表只读本地任务，待确认结果也不外发查询', async () => {
    const store = useWorkflowPublishingStore()
    vi.mocked(fetchPublishJobs).mockResolvedValue({ items: [job('one'), job('done', 'success')], nextCursor: '' })
    await store.refreshPublishJobs()
    expect(reconcilePublishJob).not.toHaveBeenCalled()
    expect(fetchPublishJob).toHaveBeenCalledWith('one')
  })

  it('打开或再次打开详情不自动查询平台', async () => {
    const store = useWorkflowPublishingStore()
    store.publishJobs = [job('one'), job('two')]
    await store.selectPublishJob('two')
    expect(reconcilePublishJob).not.toHaveBeenCalled()
    await store.selectPublishJob('two')
    expect(reconcilePublishJob).not.toHaveBeenCalled()
  })

  it('手动查询异常不会清空已有发布状态', async () => {
    const store = useWorkflowPublishingStore()
    const original = job('one')
    store.publishJobs = [original]
    vi.mocked(reconcilePublishJob).mockRejectedValue(new Error('网络断开'))
    await store.reconcileSelectedPublishJob('one', 'ozon')
    expect(reconcilePublishJob).toHaveBeenCalledWith('one', 'ozon')
    expect(store.publishJobs[0]).toEqual(original)
    expect(store.publishJobsLoading).toBe(false)
  })
})
