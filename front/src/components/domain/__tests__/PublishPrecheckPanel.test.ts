// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import PublishPrecheckPanel from '@/components/domain/PublishPrecheckPanel.vue'
import { createEmptyDraftDetail, createEmptyDraftProductContext } from '@/constants/initialState'
import type { MarketplaceTargetSite } from '@/types/workflow'

const target: MarketplaceTargetSite = {
  platform: 'ozon',
  site: 'global',
  language: 'ru-RU',
  currency: 'RUB',
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
      props: panelProps(),
    })

    const buttons = wrapper.findAll('button')
    await buttons.find((button) => button.text() === '上架预检')!.trigger('click')
    await buttons.find((button) => button.text() === 'Payload 预览')!.trigger('click')

    expect(wrapper.emitted('precheck')).toHaveLength(1)
    expect(wrapper.emitted('previewPayload')).toHaveLength(1)
    expect(buttons.find((button) => button.text() === '确认加入队列')!.attributes('disabled')).toBeDefined()
  })
})
