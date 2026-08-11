// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import DraftBoxPanel from '@/components/domain/DraftBoxPanel.vue'
import type { DraftIndexItem, MarketplaceOption } from '@/types/workflow'

const platformOptions: MarketplaceOption[] = [{
  key: 'mercadolibre',
  label: '美客多',
  sites: [
    { key: 'CBT', code: 'CBT', label: '全局', language: 'es', marketCurrency: 'USD', listingCurrency: 'USD' },
    { key: 'MLM', code: 'MLM', label: '墨西哥', language: 'es', marketCurrency: 'MXN', listingCurrency: 'MXN' },
    { key: 'MLB', code: 'MLB', label: '巴西', language: 'pt-BR', marketCurrency: 'BRL', listingCurrency: 'BRL' },
  ],
}]

function draft(draftId: string, status: string, title: string): DraftIndexItem {
  return {
    draftId,
    productId: `product-${draftId}`,
    sourceProductId: `source-${draftId}`,
    platform: 'mercadolibre',
    platforms: ['mercadolibre'],
    targetSites: [],
    site: 'MLM',
    language: 'es',
    status,
    title,
    productTitle: title,
    mainImage: '',
    sourcePlatform: '1688',
    sourceUrl: '',
    categoryId: '',
    categoryPath: '',
    publishStatus: '',
    createdAt: '',
    updatedAt: '',
    productFilePath: '',
    raw: {},
  }
}

describe('DraftBoxPanel', () => {
  it('活动草稿不会因新增或中间状态而从草稿箱消失', async () => {
    const wrapper = mount(DraftBoxPanel, {
      props: {
        drafts: [
          draft('category', 'category_ready', '类目已选择'),
          draft('future', 'awaiting_review', '待人工复核'),
          draft('published', 'published', '已发布商品'),
        ],
        platformOptions: [],
        loading: false,
      },
    })

    expect(wrapper.text()).toContain('类目已选择')
    expect(wrapper.text()).toContain('待人工复核')
    expect(wrapper.text()).not.toContain('已发布商品')
    expect(wrapper.text()).toContain('待处理：2')
    expect(wrapper.text()).toContain('已发布：1')

    await wrapper.findAll('select')[1].setValue('published')

    expect(wrapper.text()).not.toContain('类目已选择')
    expect(wrapper.text()).not.toContain('待人工复核')
    expect(wrapper.text()).toContain('已发布商品')
  })

  it('通过共享的两个选择器分别更新语言和市场', async () => {
    const row = draft('editable', 'claimed', '可编辑草稿')
    row.targetSites = [{ platform: 'mercadolibre', site: 'MLM', language: 'es', marketCurrency: 'MXN', listingCurrency: 'MXN' }]
    const wrapper = mount(DraftBoxPanel, {
      props: {
        drafts: [row],
        platformOptions,
        loading: false,
      },
    })

    await wrapper.get('[data-testid="draft-language-select"]').setValue('pt-BR')
    expect(wrapper.emitted('updateLanguage')).toEqual([[row, 'pt-BR']])

    await wrapper.get('[data-testid="draft-market-select-button"]').trigger('click')
    const checkboxes = wrapper.findAll('[data-testid="draft-target-checkbox"]')
    await checkboxes[0].setValue(true)
    await wrapper.get('[data-testid="draft-market-select-save"]').trigger('click')

    expect(wrapper.emitted('updateTargets')?.[0]?.[0]).toEqual(row)
    expect(wrapper.emitted('updateTargets')?.[0]?.[1]).toEqual([
      expect.objectContaining({ platform: 'mercadolibre', site: 'CBT' }),
      expect.objectContaining({ platform: 'mercadolibre', site: 'MLM' }),
    ])
  })
})
