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

  it('编辑包装尺寸时同步草稿并通知父组件更新核价输入', async () => {
    const props = panelProps()
    props.draft.packageDimensions = {
      lengthCm: '60',
      widthCm: '55',
      heightCm: '155',
      weightKg: '0.182',
    }
    const wrapper = mount(PublishPrecheckPanel, { props })
    const updates = [
      ['lengthCm', '61'],
      ['widthCm', '56'],
      ['heightCm', '156'],
      ['weightKg', '0.183'],
    ] as const

    for (const [field, value] of updates) {
      await wrapper.get(`input[data-package-dimension-field="${field}"]`).setValue(` ${value} `)
      expect(props.draft.packageDimensions[field]).toBe(value)
    }

    expect(wrapper.emitted('updatePackageDimension')).toEqual(
      updates.map(([field, value]) => [field, value]),
    )
    expect(wrapper.emitted('invalidatePublishValidation')).toHaveLength(updates.length)
    for (const [field] of updates) {
      const input = wrapper.get(`input[data-package-dimension-field="${field}"]`)
      expect(input.element.parentElement?.tagName).toBe('DIV')
      wrapper.get(`label[for="${input.attributes('id')}"]`)
    }
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

    await wrapper.get('[data-publish-draft-field="sku"]').setValue('SKU-NEW')
    await wrapper.get('[data-publish-draft-field="stock"]').setValue('12')
    await wrapper.get('[data-publish-draft-field="upc"]').setValue('123456789012')
    await wrapper.get('[data-publish-draft-field="allowGtinExemption"]').setValue(true)
    await wrapper.get('[data-publish-draft-field="warrantyType"]').setValue('seller')
    await wrapper.get('[data-publish-draft-field="warrantyDuration"]').setValue('6')
    await wrapper.get('[data-publish-draft-field="warrantyUnit"]').setValue('years')

    expect(wrapper.emitted('invalidatePublishValidation')).toHaveLength(7)
  })

  it('仅在当前预检通过或草稿已 ready 时允许准备素材并预览 Payload', async () => {
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
    expect(text).toContain('图片 3 张')
    expect(text).toContain(`${preview.validationDigest.slice(0, 16)}…`)
    expect(text).toContain('"offerId": "YDX-001"')

    const publishButton = wrapper.findAll('button').find((button) => button.text() === '确认加入队列')!
    expect(publishButton.attributes('disabled')).toBeUndefined()
    await publishButton.trigger('click')
    expect(wrapper.emitted('publish')).toHaveLength(1)
  })
})
