// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import CategoryPrecheckPanel from '@/components/domain/CategoryPrecheckPanel.vue'

describe('CategoryPrecheckPanel', () => {
  it('对旧结果中 errors 与 missingFields 的重复项去重', () => {
    const wrapper = mount(CategoryPrecheckPanel, {
      props: {
        result: {
          ok: false,
          errors: ['attributes.BRAND'],
          missingFields: ['attributes.BRAND'],
          checkedAt: '2026-08-04T00:00:00Z',
          raw: {},
        },
      },
    })

    expect(wrapper.findAll('li')).toHaveLength(1)
    expect(wrapper.get('li').text()).toBe('attributes.BRAND')
  })

  it('点击属性缺项时返回属性 ID', async () => {
    const wrapper = mount(CategoryPrecheckPanel, {
      props: {
        result: {
          ok: false,
          errors: [],
          missingFields: ['attributes.9048', 'category_id'],
          checkedAt: '2026-08-04T00:00:00Z',
          raw: {},
        },
      },
    })

    const attributeButton = wrapper.findAll('button').find((button) => button.text() === 'attributes.9048')
    expect(attributeButton).toBeDefined()
    await attributeButton!.trigger('click')

    expect(wrapper.emitted('locateAttribute')).toEqual([['9048']])
    expect(wrapper.findAll('button').some((button) => button.text() === 'category_id')).toBe(false)
  })
})
