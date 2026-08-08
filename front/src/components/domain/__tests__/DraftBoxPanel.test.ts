// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import DraftBoxPanel from '@/components/domain/DraftBoxPanel.vue'
import type { DraftIndexItem } from '@/types/workflow'

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
})
