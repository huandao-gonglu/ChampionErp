import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useWorkflowPublishingStore } from '@/stores/workflow/publishing'
import { fetchPublishJob, fetchPublishJobs, reconcilePublishJob } from '@/api/workflow/publishing'
import type { PublishJobListItem } from '@/types/workflow'

vi.mock('@/api/workflow/publishing', () => ({
  fetchPublishJob: vi.fn(), fetchPublishJobs: vi.fn(), reconcilePublishJob: vi.fn(),
  fetchMercadoLibreOrders: vi.fn(), fetchPublishLogs: vi.fn(),
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

  it('进入列表只检查本页未确认任务，不检查已结束任务', async () => {
    const store = useWorkflowPublishingStore()
    vi.mocked(fetchPublishJobs).mockResolvedValue({ items: [job('one'), job('done', 'success')], nextCursor: '' })
    await store.refreshPublishJobs({ checkOnView: true })
    expect(reconcilePublishJob).toHaveBeenCalledTimes(1)
    expect(reconcilePublishJob).toHaveBeenCalledWith('one', 'ozon', 'view')
    expect(fetchPublishJob).toHaveBeenCalledWith('one')
  })

  it('打开或再次打开详情只检查选中任务，服务端负责时间门槛', async () => {
    const store = useWorkflowPublishingStore()
    store.publishJobs = [job('one'), job('two')]
    await store.selectPublishJob('two')
    expect(reconcilePublishJob).toHaveBeenCalledTimes(1)
    expect(reconcilePublishJob).toHaveBeenCalledWith('two', 'ozon', 'view')
    await store.selectPublishJob('two')
    expect(reconcilePublishJob).toHaveBeenCalledTimes(2)
    expect(reconcilePublishJob).not.toHaveBeenCalledWith('one', 'ozon', 'view')
  })

  it('查看查询异常不会清空已有发布状态', async () => {
    const store = useWorkflowPublishingStore()
    const original = job('one')
    store.publishJobs = [original]
    vi.mocked(reconcilePublishJob).mockRejectedValue(new Error('网络断开'))
    await store.selectPublishJob('one')
    expect(store.publishJobs[0]).toEqual(original)
    expect(store.publishJobsLoading).toBe(false)
  })
})
