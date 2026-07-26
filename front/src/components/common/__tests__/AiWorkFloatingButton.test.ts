import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import AiWorkFloatingButton from '../AiWorkFloatingButton.vue'

describe('AiWorkFloatingButton', () => {
  it('使用新标签页打开 AI Work', () => {
    const wrapper = mount(AiWorkFloatingButton)
    const link = wrapper.get('a')

    expect(link.attributes('href')).toBe('/aiWork')
    expect(link.attributes('target')).toBe('_blank')
    expect(link.attributes('rel')).toContain('noopener')
    expect(link.attributes('aria-label')).toBe('打开 AI 对话记录')
  })
})
