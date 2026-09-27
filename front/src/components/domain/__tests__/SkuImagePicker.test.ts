import { afterEach, describe, expect, it } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import SkuImagePicker from '../SkuImagePicker.vue'

let wrapper: VueWrapper | undefined
afterEach(() => { wrapper?.unmount(); wrapper = undefined })

describe('SKU 图片选择弹窗', () => {
  it('拖出内容区不会关闭或修改选图，真正点击遮罩才关闭', async () => {
    wrapper = mount(SkuImagePicker, { props: { modelValue: '原图片' }, global: { stubs: { Teleport: true } } })
    await wrapper.get('button[aria-label="选择SKU 图片"]').trigger('click')
    const pointer = { button: 0, pointerId: 1, isPrimary: true }
    const overlay = wrapper.get('[role="dialog"]')
    await overlay.get('section h3').trigger('pointerdown', pointer)
    await overlay.trigger('pointerup', pointer)
    await overlay.trigger('click')
    expect(wrapper.find('[role="dialog"]').exists()).toBe(true)
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
    await overlay.trigger('pointerdown', pointer)
    await overlay.trigger('pointerup', pointer)
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
  })
})
