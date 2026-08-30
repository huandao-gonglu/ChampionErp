import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import PublishJobsPanel from '@/components/domain/PublishJobsPanel.vue'

const platformOptions = [
  {
    key: 'mercadolibre' as const,
    label: '美客多',
    sites: [
      { key: 'CBT', code: 'CBT', label: 'Global Selling 全局刊登', language: 'es' },
      { key: 'MLA', code: 'MLA', label: '阿根廷', language: 'es' },
      { key: 'MLU', code: 'MLU', label: '乌拉圭', language: 'es' },
    ],
  },
  {
    key: 'ozon' as const,
    label: 'Ozon',
    sites: [{ key: 'global', code: 'global', label: '俄罗斯', language: 'ru-RU' }],
  },
]

const job = {
  jobId: '20260806-220140-a07b517d',
  productId: 'product-1',
  productName: '测试商品',
  draftId: 'draft-1',
  status: 'failed' as const,
  rawStatus: 'completed',
  stage: 'failed',
  attempts: 1,
  error: '合同币种不匹配',
  platforms: [{
    platform: 'ozon',
    draftId: 'draft-1',
    site: 'global',
    sitesToSell: [],
    status: 'failed',
    stage: 'failed',
    attempts: 1,
    error: '合同币种不匹配',
    updatedAt: '2026-08-06 22:01:44',
  }],
  createdAt: '2026-08-06 22:01:40',
  updatedAt: '2026-08-06 22:01:44',
}

describe('PublishJobsPanel', () => {
  it('shows one business task row and its selected detail', () => {
    const wrapper = mount(PublishJobsPanel, {
      props: {
        jobs: [job],
        selectedJobId: job.jobId,
        selectedJobStatus: { job_id: job.jobId, display_status: 'failed' },
        loading: false,
        nextCursor: '',
        lastUpdated: '2026-08-06T22:01:45Z',
        precheckOk: true,
        activeMarketplace: 'ozon',
        platformOptions,
        busy: false,
      },
    })

    expect(wrapper.text()).toContain(job.jobId)
    expect(wrapper.text()).toContain('发布失败')
    expect(wrapper.text()).toContain('合同币种不匹配')
    expect(wrapper.text()).toContain('Ozon · 俄罗斯（global）')
    expect(wrapper.findAll('select')[1].get('option[value="ozon"]').text()).toBe('Ozon')
    expect(wrapper.text()).not.toContain('completed')
  })

  it('同一 CBT 父刊登按销售子市场区分阿根廷和乌拉圭任务', () => {
    const argentinaJob = {
      ...job,
      jobId: '20260829-203906-86e8145e',
      draftId: 'd12fb1fe48cb6',
      status: 'success' as const,
      error: '',
      platforms: [{
        ...job.platforms[0],
        platform: 'mercadolibre',
        draftId: 'd12fb1fe48cb6',
        site: 'CBT',
        sitesToSell: [{ siteId: 'MLA', logisticType: 'remote' }],
        status: 'success',
        stage: 'finished',
        error: '',
      }],
    }
    const uruguayJob = {
      ...job,
      jobId: '20260829-204353-9e0373b7',
      draftId: 'd5cc0d58cb7bd',
      platforms: [{
        ...job.platforms[0],
        platform: 'mercadolibre',
        draftId: 'd5cc0d58cb7bd',
        site: 'CBT',
        sitesToSell: [{ siteId: 'MLU', logisticType: 'remote' }],
      }],
    }
    const wrapper = mount(PublishJobsPanel, {
      props: {
        jobs: [uruguayJob, argentinaJob],
        selectedJobId: uruguayJob.jobId,
        selectedJobStatus: null,
        loading: false,
        nextCursor: '',
        lastUpdated: '',
        precheckOk: true,
        activeMarketplace: 'mercadolibre',
        platformOptions,
        busy: false,
      },
    })

    expect(wrapper.text()).toContain('美客多 · Global Selling 全局刊登（CBT） → 乌拉圭（MLU）')
    expect(wrapper.text()).toContain('美客多 · Global Selling 全局刊登（CBT） → 阿根廷（MLA）')
  })

  it('未注册平台保留原始 key 和站点作为展示兜底', () => {
    const customJob = {
      ...job,
      platforms: [{
        ...job.platforms[0],
        platform: 'custommarket',
        site: 'BR',
        sitesToSell: [{ siteId: 'ZZ', logisticType: 'remote' }],
      }],
    }
    const wrapper = mount(PublishJobsPanel, {
      props: {
        jobs: [customJob],
        selectedJobId: customJob.jobId,
        selectedJobStatus: null,
        loading: false,
        nextCursor: '',
        lastUpdated: '',
        precheckOk: true,
        activeMarketplace: 'custommarket',
        platformOptions,
        busy: false,
      },
    })

    expect(wrapper.text()).toContain('custommarket · BR → ZZ')
  })

  it('selects a job from the list instead of reusing a global detail', async () => {
    const wrapper = mount(PublishJobsPanel, {
      props: {
        jobs: [job],
        selectedJobId: '',
        selectedJobStatus: null,
        loading: false,
        nextCursor: '',
        lastUpdated: '',
        precheckOk: false,
        activeMarketplace: 'ozon',
        platformOptions,
        busy: false,
      },
    })

    await wrapper.get(`button[title="${job.jobId}"]`).trigger('click')

    expect(wrapper.emitted('select')).toEqual([[job.jobId]])
  })

  it('shows platform confirmation polling as running instead of failed', () => {
    const pendingJob = {
      ...job,
      status: 'running' as const,
      rawStatus: 'running',
      stage: 'waiting_platform_confirmation',
      error: '',
      platforms: [{
        ...job.platforms[0],
        status: 'running',
        stage: 'waiting_platform_confirmation',
        error: '',
      }],
    }
    const wrapper = mount(PublishJobsPanel, {
      props: {
        jobs: [pendingJob],
        selectedJobId: pendingJob.jobId,
        selectedJobStatus: {
          job_id: pendingJob.jobId,
          display_status: 'running',
        },
        loading: false,
        nextCursor: '',
        lastUpdated: '',
        precheckOk: true,
        activeMarketplace: 'ozon',
        platformOptions,
        busy: false,
      },
    })

    expect(wrapper.text()).toContain('发布中')
    expect(wrapper.text()).toContain('等待平台确认')
    expect(wrapper.text()).not.toContain('失败原因')
  })

  it('only offers read-only reconciliation for an outcome-unknown platform', async () => {
    const unknownJob = {
      ...job,
      status: 'outcome_unknown' as const,
      rawStatus: 'outcome_unknown',
      stage: 'outcome_unknown',
      error: '远端终态未知',
      platforms: [{
        ...job.platforms[0],
        platform: 'mercadolibre' as const,
        site: 'CBT',
        status: 'outcome_unknown',
        stage: 'outcome_unknown',
        error: '远端终态未知',
      }],
    }
    const wrapper = mount(PublishJobsPanel, {
      props: {
        jobs: [unknownJob],
        selectedJobId: unknownJob.jobId,
        selectedJobStatus: { job_id: unknownJob.jobId },
        loading: false,
        nextCursor: '',
        lastUpdated: '',
        precheckOk: true,
        activeMarketplace: 'mercadolibre',
        platformOptions,
        busy: false,
      },
    })

    expect(wrapper.text()).toContain('不会再次提交创建或更新请求')
    expect(wrapper.text()).toContain('美客多 · Global Selling 全局刊登（CBT）')
    expect(wrapper.text()).not.toContain('mercadolibre')
    await wrapper.get('[data-testid="publish-job-reconcile"]').trigger('click')
    expect(wrapper.emitted('reconcile')).toEqual([
      [unknownJob.jobId, 'mercadolibre'],
    ])
  })
})
