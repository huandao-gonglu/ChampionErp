// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import PublishPrecheckPanel from '@/components/domain/PublishPrecheckPanel.vue'
import { createEmptyDraftDetail, createEmptyDraftProductContext } from '@/constants/initialState'
import type { MarketplaceTargetSite, PayloadPreviewState, PublishPrecheck } from '@/types/workflow'

const target: MarketplaceTargetSite = {
  platform: 'ozon',
  site: 'global',
  language: 'ru-RU',
  listingCurrency: 'RUB',
}

function panelProps() {
  const draft = createEmptyDraftDetail('ozon')
  draft.draftId = 'draft-precheck'
  draft.site = 'global'
  return {
    draft,
    productContext: createEmptyDraftProductContext(),
    publishTargets: [target],
    selectedPublishTarget: target,
    platformOptions: [],
    precheck: null,
    payloadPreview: null,
    loading: false,
  }
}

function passedPrecheck(): PublishPrecheck {
  return { ok: true, errors: [], warnings: [], errorItems: [], warningItems: [], checkedAt: '2026-08-04T00:00:00Z' }
}

function layeredPrecheck(ok = true): PublishPrecheck {
  return {
    ok,
    errors: ok ? [] : ['阿根廷售价无效（重新核价）'],
    warnings: [],
    errorItems: ok ? [] : [{
      code: 'MARKET_PRICE_INVALID',
      field: 'price',
      message: '阿根廷售价无效',
      severity: 'error',
      nextAction: '重新核价',
    }],
    warningItems: [],
    checkedAt: '2026-08-30T00:00:00Z',
    parent: {
      ok: true,
      status: 'passed',
      errors: [],
      warnings: [],
    },
    marketChecks: [
      {
        siteId: 'MLA',
        logisticType: 'remote',
        ok,
        status: ok ? 'passed' : 'blocked',
        errors: ok ? [] : [{
          code: 'MARKET_PRICE_INVALID',
          field: 'price',
          message: '阿根廷售价无效',
          severity: 'error',
          nextAction: '重新核价',
        }],
        warnings: [],
      },
      {
        siteId: 'MLC',
        logisticType: 'remote',
        ok: true,
        status: 'passed',
        errors: [],
        warnings: [{
          code: 'SHIPPING_NOTICE',
          field: 'shipping',
          message: '请复核智利市场运费报价',
          severity: 'warning',
          nextAction: '返回核价页确认运费报价',
        }],
      },
    ],
  }
}

function payloadPreview(): PayloadPreviewState {
  return {
    platform: 'yandex',
    site: 'global',
    targetKey: 'yandex:global',
    status: 'preview_only',
    path: 'logs/payload.json',
    payload: { offerId: 'YDX-001' },
    warning: '',
    validationDigest: 'a1b2c3d4e5f60718'.repeat(4),
    summary: {
      productId: 'prod-1',
      draftId: 'draft-precheck',
      platform: 'yandex',
      site: 'global',
      storeIdentity: 'yandex:9f8e7d6c5b4a3210',
      storeLabel: '示例店铺',
      title: 'Настольный вентилятор',
      categoryId: '91596',
      listingCurrency: 'RUB',
      price: '1299',
      stock: '10',
      imageCount: 3,
      groupingMode: 'separate',
      skuItems: [{ sku_id: 'first', sku: 'YDX-001', stock: '10', price: '1299', currency: 'RUB', destinations: [] }, { sku_id: 'second', sku: 'YDX-002', stock: '5', price: '1599', currency: 'RUB', destinations: [] }],
    },
    warnings: [],
  }
}

describe('PublishPrecheckPanel', () => {
  it('只渲染发布预检，不包含类目属性编辑模块', () => {
    const wrapper = mount(PublishPrecheckPanel, {
      props: panelProps(),
    })

    expect(wrapper.text()).toContain('发布必填资料')
    expect(wrapper.text()).toContain('预检结果')
    expect(wrapper.text()).toContain('Payload 预览')
    expect(wrapper.text()).not.toContain('类目候选与手动搜索')
    expect(wrapper.text()).not.toContain('当前类目 / 平台属性')
    expect(wrapper.text()).not.toContain('AI 填充属性')
    expect(wrapper.text()).not.toContain('类目预检')
  })

  it('保留发布预检、Payload 预览和入队事件', async () => {
    const wrapper = mount(PublishPrecheckPanel, {
      props: {
        ...panelProps(),
        precheck: passedPrecheck(),
      },
    })

    const buttons = wrapper.findAll('button')
    await buttons.find((button) => button.text() === '上架预检')!.trigger('click')
    await buttons.find((button) => button.text() === '准备素材并预览 Payload')!.trigger('click')

    expect(wrapper.emitted('precheck')).toHaveLength(1)
    expect(wrapper.emitted('previewPayload')).toHaveLength(1)
    expect(buttons.find((button) => button.text() === '确认加入队列')!.attributes('disabled')).toBeDefined()
  })

  it('预检读取所选 SKU，不提供草稿级单品编码或包装编辑入口', () => {
    const wrapper = mount(PublishPrecheckPanel, { props: panelProps() })
    expect(wrapper.find('[data-package-dimension-field]').exists()).toBe(false)
    expect(wrapper.find('[data-publish-draft-field="sku"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('逐 SKU 校验')
  })

  it('任一发布字段编辑都会通知父组件废弃旧预检与 Payload', async () => {
    const props = panelProps()
    props.draft.status = 'ready_to_publish'
    const wrapper = mount(PublishPrecheckPanel, {
      props: {
        ...props,
        precheck: passedPrecheck(),
        payloadPreview: payloadPreview(),
      },
    })

    await wrapper.get('[data-publish-draft-field="allowGtinExemption"]').setValue(true)
    await wrapper.get('[data-publish-draft-field="warrantyType"]').setValue('seller')
    await wrapper.get('[data-publish-draft-field="warrantyDuration"]').setValue('6')
    await wrapper.get('[data-publish-draft-field="warrantyUnit"]').setValue('years')

    expect(wrapper.emitted('invalidatePublishValidation')).toHaveLength(4)
  })

  it('未配置保修条款时显示未选择，不把空数据伪装成无保修', () => {
    const props = panelProps()
    props.draft.saleTerms = []
    const wrapper = mount(PublishPrecheckPanel, { props })

    const warrantyType = wrapper.get('[data-publish-draft-field="warrantyType"]')
    const warrantyDuration = wrapper.get('[data-publish-draft-field="warrantyDuration"]')
    const warrantyUnit = wrapper.get('[data-publish-draft-field="warrantyUnit"]')

    expect((warrantyType.element as HTMLSelectElement).value).toBe('')
    expect((warrantyDuration.element as HTMLInputElement).value).toBe('')
    expect((warrantyUnit.element as HTMLSelectElement).value).toBe('')
    expect(wrapper.text()).toContain('尚未选择保修类型')
    expect(warrantyDuration.attributes('disabled')).toBeDefined()
    expect(warrantyUnit.attributes('disabled')).toBeDefined()
    expect(props.draft.saleTerms).toEqual([])
  })

  it('明确选择无保修后才把 Mercado Libre 保修声明写入草稿', async () => {
    const props = panelProps()
    props.draft.saleTerms = []
    const wrapper = mount(PublishPrecheckPanel, { props })

    await wrapper.get('[data-publish-draft-field="warrantyType"]').setValue('none')

    expect(props.draft.saleTerms).toEqual([
      { id: 'WARRANTY_TYPE', value_id: '6150835', value_name: 'Sin garantía' },
    ])
    expect(wrapper.emitted('invalidatePublishValidation')).toHaveLength(1)
    expect(wrapper.text()).toContain('已明确选择无保修')
  })

  it('选择卖家保修时把界面默认时长同时写入草稿，不留下仅显示的假默认', async () => {
    const props = panelProps()
    props.draft.saleTerms = []
    const wrapper = mount(PublishPrecheckPanel, { props })

    await wrapper.get('[data-publish-draft-field="warrantyType"]').setValue('seller')

    expect(props.draft.saleTerms).toEqual([
      { id: 'WARRANTY_TYPE', value_id: '2230280', value_name: 'Garantía del vendedor' },
      {
        id: 'WARRANTY_TIME',
        value_name: '3 meses',
        value_struct: { number: 3, unit: 'meses' },
      },
    ])
    expect((wrapper.get('[data-publish-draft-field="warrantyDuration"]').element as HTMLInputElement).value).toBe('3')
    expect((wrapper.get('[data-publish-draft-field="warrantyUnit"]').element as HTMLSelectElement).value).toBe('months')
  })

  it('当前草稿摘要不使用来源商品标题和 SKU 冒充草稿字段', () => {
    const props = panelProps()
    props.draft.title = ''
    props.draft.sku = ''
    props.productContext.title = '来源商品标题'
    props.productContext.sourceTitle = '1688 来源标题'
    props.productContext.sku = 'SOURCE-SKU'
    const wrapper = mount(PublishPrecheckPanel, { props })

    expect(wrapper.text()).toContain('草稿标题未填写')
    expect(wrapper.text()).toContain('已选 0 个 SKU')
    expect(wrapper.text()).toContain('1688 来源标题')
    expect(wrapper.text()).not.toContain('SOURCE-SKU')
  })

  it('其他平台仍可从已持久化的 ready 状态继续准备 Payload', async () => {
    const wrapper = mount(PublishPrecheckPanel, { props: panelProps() })
    const previewButton = () => wrapper.findAll('button').find((button) => button.text() === '准备素材并预览 Payload')!

    expect(previewButton().attributes('disabled')).toBeDefined()
    await previewButton().trigger('click')
    expect(wrapper.emitted('previewPayload')).toBeUndefined()

    await wrapper.setProps({ precheck: passedPrecheck() })
    expect(previewButton().attributes('disabled')).toBeUndefined()

    await wrapper.setProps({ precheck: null })
    wrapper.props('draft').status = 'ready_to_publish'
    await wrapper.vm.$nextTick()
    expect(previewButton().attributes('disabled')).toBeUndefined()
  })

  it('Mercado 分层预检为空时不允许 stale ready_to_publish 绕过', () => {
    const props = panelProps()
    props.draft.platform = 'mercadolibre'
    props.draft.site = 'CBT'
    props.draft.status = 'ready_to_publish'
    const mercadoTarget: MarketplaceTargetSite = {
      platform: 'mercadolibre',
      site: 'CBT',
      language: 'en-US',
      listingCurrency: 'USD',
    }
    const wrapper = mount(PublishPrecheckPanel, {
      props: {
        ...props,
        publishTargets: [mercadoTarget],
        selectedPublishTarget: mercadoTarget,
        precheck: null,
      },
    })

    const previewButton = wrapper.findAll('button').find((button) => button.text() === '准备素材并预览 Payload')!
    expect(previewButton.attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('点击上架预检后')
    expect(wrapper.text()).not.toContain('已保存为校验通过')
  })

  it('预检通过但没有 Payload 确认指纹时仍禁止入队', () => {
    const wrapper = mount(PublishPrecheckPanel, {
      props: {
        ...panelProps(),
        precheck: passedPrecheck(),
      },
    })

    const publishButton = wrapper.findAll('button').find((button) => button.text() === '确认加入队列')!
    expect(publishButton.attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('请点击 Payload 预览生成确认摘要')
  })

  it('有分层 scope 时分别展示父级、销售市场和提醒项', () => {
    const props = panelProps()
    const mercadoTarget: MarketplaceTargetSite = {
      platform: 'mercadolibre',
      site: 'CBT',
      language: 'es',
      listingCurrency: 'USD',
    }
    const wrapper = mount(PublishPrecheckPanel, {
      props: {
        ...props,
        publishTargets: [mercadoTarget],
        selectedPublishTarget: mercadoTarget,
        platformOptions: [{
          key: 'mercadolibre',
          label: '美客多',
          sites: [
            { key: 'CBT', code: 'CBT', label: 'Global Selling 全局刊登', language: 'es' },
            { key: 'MLA', code: 'MLA', label: '阿根廷', language: 'es' },
            { key: 'MLC', code: 'MLC', label: '智利', language: 'es' },
          ],
        }],
        precheck: layeredPrecheck(),
      },
    })

    expect(wrapper.get('[data-testid="publish-precheck-scopes"]').text()).toContain('共享刊登（CBT）')
    expect(wrapper.get('[data-testid="publish-precheck-scopes"]').text()).toContain('分市场检查')
    expect(wrapper.get('[data-testid="publish-precheck-scopes"]').text()).toContain('任一项不通过都不能发布')
    expect(wrapper.get('[data-testid="publish-precheck-scopes"]').text()).toContain('已知规则检查通过')
    expect(wrapper.get('[data-testid="publish-precheck-scopes"]').text()).not.toContain('分层预检')
    expect(wrapper.get('[data-testid="publish-precheck-scopes"]').text()).not.toContain('确定性')
    expect(wrapper.get('[data-testid="publish-precheck-scopes"]').text()).toContain('阿根廷（MLA）')
    expect(wrapper.get('[data-testid="publish-precheck-scopes"]').text()).toContain('智利（MLC）')
    expect(wrapper.get('[data-testid="publish-precheck-scopes"]').text()).toContain('物流方式：跨境直发')
    expect(wrapper.get('[data-testid="publish-precheck-scopes"]').text()).not.toContain('remote')
    expect(wrapper.get('[data-testid="publish-precheck-scopes"]').text()).toContain('通过')
    expect(wrapper.get('[data-testid="publish-precheck-scopes"]').text()).toContain('请复核智利市场运费报价')
    expect(wrapper.text()).toContain('预检通过。请点击 Payload 预览生成确认摘要')

    const previewButton = wrapper.findAll('button').find((button) => button.text() === '准备素材并预览 Payload')!
    expect(previewButton.attributes('disabled')).toBeUndefined()
  })

  it('主界面隐藏技术field与错误码，并按市场分别统计相同问题', () => {
    const repeatedError = {
      code: 'MERCADOLIBRE_CATEGORY_MARKET_LOGISTICS_UNSUPPORTED',
      field: 'sites_to_sell[0].category_id',
      message: '内部实验诊断不得展示',
      severity: 'error',
      nextAction: '内部技术对照不得展示',
    }
    const repeatedWarning = {
      code: 'INTERNAL_MARKET_NOTICE',
      field: 'sites_to_sell[0].shipping',
      message: '请复核市场运费报价',
      severity: 'warning',
      nextAction: '返回核价页确认运费',
    }
    const mercadoTarget: MarketplaceTargetSite = {
      platform: 'mercadolibre',
      site: 'CBT',
      language: 'es',
      listingCurrency: 'USD',
    }
    const precheck: PublishPrecheck = {
      ok: false,
      errors: [`${repeatedError.message}（${repeatedError.nextAction}）`],
      warnings: [`${repeatedWarning.message}（${repeatedWarning.nextAction}）`],
      errorItems: [repeatedError],
      warningItems: [repeatedWarning],
      checkedAt: '2026-08-31T00:00:00Z',
      parent: { ok: true, status: 'passed', errors: [], warnings: [] },
      marketChecks: ['MCO', 'MLC'].map((siteId) => ({
        siteId,
        logisticType: 'remote',
        ok: false,
        status: 'blocked' as const,
        errors: [{ ...repeatedError }],
        warnings: [{ ...repeatedWarning }],
      })),
    }
    const wrapper = mount(PublishPrecheckPanel, {
      props: {
        ...panelProps(),
        publishTargets: [mercadoTarget],
        selectedPublishTarget: mercadoTarget,
        precheck,
      },
    })

    expect(wrapper.text()).toContain('2 项不通过 / 2 项提醒')
    expect(wrapper.text()).not.toContain(repeatedError.field)
    expect(wrapper.text()).not.toContain(repeatedError.code)
    expect(wrapper.text()).not.toContain(repeatedWarning.field)
    expect(wrapper.text()).not.toContain(repeatedWarning.code)
    expect(wrapper.text()).not.toContain('内部实验诊断不得展示')
    expect(wrapper.text()).not.toContain('内部技术对照不得展示')
    const firstMarket = wrapper.get('[data-testid="publish-precheck-scope-market:0:MCO:remote"]')
    expect(firstMarket.findAll('li')[0].findAll('p').map((item) => item.text())).toEqual([
      '原因：当前发布方式不支持该市场的跨境物流。',
      '处理建议：重新执行上架预检，并按最新的店铺、市场与物流能力结果处理。',
    ])
  })

  it('重复或空市场身份仍生成独立 scope 卡片', () => {
    const precheck = layeredPrecheck(true)
    const emptyMarket = {
      ...precheck.marketChecks![0],
      siteId: '',
      logisticType: '',
    }
    precheck.marketChecks = [emptyMarket, { ...emptyMarket }]
    const wrapper = mount(PublishPrecheckPanel, {
      props: {
        ...panelProps(),
        precheck,
      },
    })

    expect(wrapper.findAll('[data-testid^="publish-precheck-scope-market:"]')).toHaveLength(2)
  })

  it('分层预检存在错误时即使顶层 ok=true 也阻断后续按钮', () => {
    const props = panelProps()
    props.draft.status = 'ready_to_publish'
    const inconsistentPrecheck = layeredPrecheck(false)
    inconsistentPrecheck.ok = true
    inconsistentPrecheck.errors = []
    inconsistentPrecheck.errorItems = []
    const wrapper = mount(PublishPrecheckPanel, {
      props: {
        ...props,
        precheck: inconsistentPrecheck,
        payloadPreview: payloadPreview(),
      },
    })

    expect(wrapper.text()).toContain('阿根廷售价无效')
    expect(wrapper.text()).toContain('不通过')
    const previewButton = wrapper.findAll('button').find((button) => button.text() === '准备素材并预览 Payload')!
    const publishButton = wrapper.findAll('button').find((button) => button.text() === '确认加入队列')!
    expect(previewButton.attributes('disabled')).toBeDefined()
    expect(publishButton.attributes('disabled')).toBeDefined()
  })

  it('分层 status=blocked 即使没有错误明细也阻断后续按钮', () => {
    const precheck = layeredPrecheck(true)
    precheck.marketChecks![0] = {
      ...precheck.marketChecks![0],
      ok: true,
      status: 'blocked',
      errors: [],
    }
    const wrapper = mount(PublishPrecheckPanel, {
      props: {
        ...panelProps(),
        precheck,
        payloadPreview: payloadPreview(),
      },
    })

    expect(wrapper.text()).toContain('预检未通过')
    expect(wrapper.text()).toContain('还有 1 项未通过')
    const previewButton = wrapper.findAll('button').find((button) => button.text() === '准备素材并预览 Payload')!
    const publishButton = wrapper.findAll('button').find((button) => button.text() === '确认加入队列')!
    expect(previewButton.attributes('disabled')).toBeDefined()
    expect(publishButton.attributes('disabled')).toBeDefined()
  })

  it('scope 卡片存在时仍展示 normalizer 合成的顶层不一致问题', () => {
    const precheck = layeredPrecheck(false)
    precheck.errorItems.push({
      code: 'LAYERED_PRECHECK_MARKETS_MISMATCH',
      field: 'sites_to_sell',
      message: '已选销售市场与预检结果不一致',
      severity: 'error',
      nextAction: '重新执行上架预检',
    })
    precheck.errors.push('已选销售市场与预检结果不一致（重新执行上架预检）')
    precheck.warningItems.push({
      code: 'GLOBAL_WARNING',
      field: 'description',
      message: '描述建议补充',
      severity: 'warning',
      nextAction: '完善描述',
    })
    precheck.warnings.push('描述建议补充（完善描述）')
    const wrapper = mount(PublishPrecheckPanel, {
      props: {
        ...panelProps(),
        precheck,
      },
    })

    expect(wrapper.get('[data-testid="publish-precheck-top-level-errors"]').text()).toContain('已选销售市场与预检结果不一致')
    expect(wrapper.get('[data-testid="publish-precheck-top-level-errors"]').text()).not.toContain('sites_to_sell')
    expect(wrapper.get('[data-testid="publish-precheck-top-level-errors"]').text()).not.toContain('LAYERED_PRECHECK_MARKETS_MISMATCH')
    expect(wrapper.get('[data-testid="publish-precheck-top-level-warnings"]').text()).toContain('描述建议补充')
    expect(wrapper.text()).toContain('2 项不通过 / 2 项提醒')
    expect(wrapper.text().match(/阿根廷售价无效/g)).toHaveLength(1)
  })

  it('没有分层 scope 时保持其他平台的原有预检展示', () => {
    const wrapper = mount(PublishPrecheckPanel, {
      props: {
        ...panelProps(),
        precheck: passedPrecheck(),
      },
    })

    expect(wrapper.find('[data-testid="publish-precheck-scopes"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('预检通过，可以发布。')
  })

  it('预览确认后展示摘要与指纹，并允许确认入队', async () => {
    const preview = payloadPreview()
    const wrapper = mount(PublishPrecheckPanel, {
      props: {
        ...panelProps(),
        precheck: passedPrecheck(),
        payloadPreview: preview,
      },
    })

    const text = wrapper.text()
    const summary = preview.summary!
    expect(text).toContain('已确认预览')
    expect(text).toContain(summary.storeIdentity)
    expect(text).toContain('1299 RUB')
    expect(text).toContain('1599 RUB')
    expect(text).toContain('2 个 SKU · 3 张共用图片')
    expect(text).toContain(`${preview.validationDigest.slice(0, 16)}…`)
    expect(text).toContain('"offerId": "YDX-001"')

    const publishButton = wrapper.findAll('button').find((button) => button.text() === '确认加入队列')!
    expect(publishButton.attributes('disabled')).toBeUndefined()
    await publishButton.trigger('click')
    expect(wrapper.emitted('publish')).toHaveLength(1)
  })
})
