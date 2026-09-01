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
      { key: 'MCO', code: 'MCO', label: '哥伦比亚', language: 'es' },
      { key: 'MLC', code: 'MLC', label: '智利', language: 'es' },
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
  errorCode: '',
  nextAction: '',
  platforms: [{
    platform: 'ozon',
    draftId: 'draft-1',
    site: 'global',
    sitesToSell: [],
    status: 'failed',
    stage: 'failed',
    attempts: 1,
    error: '合同币种不匹配',
    errorCode: '',
    nextAction: '',
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

  it('统一多市场发布展示每个销售市场的真实结果', () => {
    const partialJob = {
      ...job,
      status: 'partial' as const,
      error: '部分销售市场创建失败',
      platforms: [{
        ...job.platforms[0],
        platform: 'mercadolibre',
        site: 'CBT',
        status: 'partial',
        stage: 'partial',
        error: '部分销售市场创建失败',
        marketResults: [
          {
            siteId: 'MLA',
            logisticType: 'remote',
            status: 'success',
            itemId: 'MLA3897014464',
            error: '',
            errorCode: '',
          },
          {
            siteId: 'MCO',
            logisticType: 'remote',
            status: 'failed',
            itemId: '',
            error: "You can't send the product in this kind of shipment in Colombia.",
            errorCode: 'item.shipping.mode.not_supported',
          },
        ],
      }],
    }
    const wrapper = mount(PublishJobsPanel, {
      props: {
        jobs: [partialJob],
        selectedJobId: partialJob.jobId,
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

    const marketResults = wrapper.get('[data-testid="publish-market-results"]')
    expect(wrapper.text()).toContain('部分成功')
    expect(marketResults.text()).toContain('阿根廷（MLA） · remote')
    expect(marketResults.text()).toContain('MLA3897014464')
    expect(marketResults.text()).toContain('哥伦比亚（MCO） · remote')
    expect(marketResults.text()).toContain('item.shipping.mode.not_supported')
    expect(marketResults.text()).not.toContain('当前发布方式不支持该市场的跨境物流')
    expect(marketResults.text()).not.toContain("You can't send the product")
    expect(wrapper.get('[data-testid="publish-job-error-guidance"]').text()).toContain('item.shipping.mode.not_supported')
    expect(wrapper.get('[data-testid="publish-platform-error-guidance"]').text()).toContain('item.shipping.mode.not_supported')
  })

  it('子市场没有返回错误码时原样展示平台错误文本', () => {
    const mercadoJob = {
      ...job,
      status: 'partial' as const,
      platforms: [{
        ...job.platforms[0],
        platform: 'mercadolibre',
        site: 'CBT',
        status: 'partial',
        marketResults: [{
          siteId: 'MLU',
          logisticType: 'remote',
          status: 'failed',
          itemId: '',
          error: 'Listing in Uruguay is currently unavailable for international dropshipping',
          errorCode: '',
        }],
      }],
    }
    const wrapper = mount(PublishJobsPanel, {
      props: {
        jobs: [mercadoJob],
        selectedJobId: mercadoJob.jobId,
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

    const marketResults = wrapper.get('[data-testid="publish-market-results"]')
    expect(marketResults.text()).toContain('Listing in Uruguay is currently unavailable for international dropshipping')
    expect(marketResults.text()).not.toContain('处理建议')
  })

  it('将平台的物流方式错误转换为不猜测类目根因的说明', () => {
    const mercadoJob = {
      ...job,
      jobId: '20260830-mercado-category-market-logistics',
      error: 'MCO/remote: item.shipping.mode.not_supported',
      platforms: [{
        ...job.platforms[0],
        platform: 'mercadolibre',
        site: 'CBT',
        sitesToSell: [
          { siteId: 'MCO', logisticType: 'remote' },
          { siteId: 'MLC', logisticType: 'remote' },
        ],
        error: 'MCO/remote: item.shipping.mode.not_supported',
      }],
    }
    const wrapper = mount(PublishJobsPanel, {
      props: {
        jobs: [mercadoJob],
        selectedJobId: mercadoJob.jobId,
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

    expect(wrapper.text()).toContain('当前发布方式不支持该市场的跨境物流')
    expect(wrapper.text()).toContain('检查店铺、销售市场与物流能力')
    expect(wrapper.text()).not.toContain('共享 CBT 类目不支持')
    expect(wrapper.text()).not.toContain('MERCADOLIBRE_CATEGORY_MARKET_LOGISTICS_UNSUPPORTED')
    expect(wrapper.text()).not.toContain('MCO/remote: item.shipping.mode.not_supported')
    expect(wrapper.get('[data-testid="publish-job-error-guidance"]').findAll('p').map((item) => item.text())).toEqual([
      '失败原因',
      '原因：当前发布方式不支持该市场的跨境物流。',
      '处理建议：检查店铺、销售市场与物流能力；确认不支持时移除该市场。',
    ])
    expect(wrapper.get('[data-testid="publish-platform-error-guidance"]').findAll('p').map((item) => item.text())).toEqual([
      '原因：当前发布方式不支持该市场的跨境物流。',
      '处理建议：检查店铺、销售市场与物流能力；确认不支持时移除该市场。',
    ])
  })

  it.each([
    ['MERCADOLIBRE_SHIPPING_MODE_NOT_SUPPORTED', '当前发布方式不支持该市场的跨境物流', '后端返回的处理建议'],
    ['MERCADOLIBRE_CATEGORY_MARKET_LOGISTICS_UNSUPPORTED', '当前发布方式不支持该市场的跨境物流', '检查店铺、销售市场与物流能力'],
    ['MERCADOLIBRE_MARKET_NOT_OPERABLE', '该销售市场当前不支持国际跨境直发', '后端返回的处理建议'],
    ['MERCADOLIBRE_PACKAGE_CARRIER_LIMIT_EXCEEDED', '发货包装的尺寸或重量超过当前物流限制', '后端返回的处理建议'],
    ['MERCADOLIBRE_LOCAL_RATE_LIMITED', '平台当前请求受限', '后端返回的处理建议'],
  ])('业务错误 %s 只展示简短原因和可执行建议', (errorCode, summary, expectedAction) => {
    const codedJob = {
      ...job,
      error: 'remote raw error payload',
      errorCode,
      nextAction: '后端返回的处理建议',
      platforms: [{
        ...job.platforms[0],
        error: 'remote raw error payload',
        errorCode,
        nextAction: '后端返回的处理建议',
      }],
    }
    const wrapper = mount(PublishJobsPanel, {
      props: {
        jobs: [codedJob],
        selectedJobId: codedJob.jobId,
        selectedJobStatus: null,
        loading: false,
        nextCursor: '',
        lastUpdated: '',
        precheckOk: true,
        activeMarketplace: 'ozon',
        platformOptions,
        busy: false,
      },
    })

    const guidance = wrapper.get('[data-testid="publish-job-error-guidance"]')
    expect(guidance.text()).toContain(summary)
    expect(guidance.text()).toContain(`处理建议：${expectedAction}`)
    expect(guidance.text()).not.toContain(errorCode)
    expect(guidance.text()).not.toContain('remote raw error payload')
  })

  it('技术错误只出现在折叠的技术详情中', () => {
    const technicalJob = {
      ...job,
      error: 'HTTP 400: remote.validation.failed',
      errorCode: 'REMOTE_VALIDATION_FAILED',
      nextAction: '检查草稿后重新预检',
      platforms: [{
        ...job.platforms[0],
        error: 'HTTP 400: remote.validation.failed',
        errorCode: 'REMOTE_VALIDATION_FAILED',
        nextAction: '检查草稿后重新预检',
      }],
    }
    const wrapper = mount(PublishJobsPanel, {
      props: {
        jobs: [technicalJob],
        selectedJobId: technicalJob.jobId,
        selectedJobStatus: {
          job_id: technicalJob.jobId,
          error_code: 'REMOTE_VALIDATION_FAILED',
          raw_error: 'HTTP 400: remote.validation.failed',
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

    const mainGuidance = wrapper.get('[data-testid="publish-job-error-guidance"]')
    expect(mainGuidance.text()).toContain('发布失败，平台未返回可直接展示的原因')
    expect(mainGuidance.text()).toContain('处理建议：检查草稿后重新预检')
    expect(mainGuidance.text()).not.toContain('REMOTE_VALIDATION_FAILED')
    expect(mainGuidance.text()).not.toContain('HTTP 400: remote.validation.failed')
    const technicalDetails = wrapper.get('details')
    expect(technicalDetails.text()).toContain('查看技术详情')
    expect(technicalDetails.text()).toContain('REMOTE_VALIDATION_FAILED')
    expect(technicalDetails.text()).toContain('HTTP 400: remote.validation.failed')
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
