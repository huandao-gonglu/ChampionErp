import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import OrderThumbnail from '../OrderThumbnail.vue'

describe('订单商品缩略图', () => {
  it('缺图与加载失败显示占位，切换 SKU 后可重新加载', async () => {
    const wrapper = mount(OrderThumbnail, { props: { title: '红色 M' } })
    expect(wrapper.find('img').exists()).toBe(false)
    expect(wrapper.get('[role="img"]').attributes('aria-label')).toBe('红色 M：暂无图片')
    await wrapper.setProps({ src: 'https://images.example/red.jpg' })
    expect(wrapper.get('img').attributes('alt')).toBe('红色 M')
    await wrapper.get('img').trigger('error')
    expect(wrapper.find('img').exists()).toBe(false)
    await wrapper.setProps({ src: 'https://images.example/blue.jpg', title: '蓝色 L' })
    expect(wrapper.get('img').attributes('src')).toContain('blue.jpg')
  })
})
