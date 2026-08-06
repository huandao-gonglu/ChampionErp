import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import PublishJobsPanel from '@/components/domain/PublishJobsPanel.vue'

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
        busy: false,
      },
    })

    expect(wrapper.text()).toContain(job.jobId)
    expect(wrapper.text()).toContain('发布失败')
    expect(wrapper.text()).toContain('合同币种不匹配')
    expect(wrapper.text()).not.toContain('completed')
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
        busy: false,
      },
    })

    await wrapper.get(`button[title="${job.jobId}"]`).trigger('click')

    expect(wrapper.emitted('select')).toEqual([[job.jobId]])
  })
})
