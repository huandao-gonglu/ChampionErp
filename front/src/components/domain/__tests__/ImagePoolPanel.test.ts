import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import ImagePoolPanel from '@/components/domain/ImagePoolPanel.vue'
import type { ImageAsset } from '@/types/workflow'
enableAutoUnmount(afterEach)

function image(id: string): ImageAsset {
  return {
    id,
    url: `/images/${id}.png`,
    path: '',
    previewUrl: `/images/${id}.png`,
    origin: 'upload',
    usage: 'detail',
    platforms: ['mercadolibre'],
    isMain: false,
    selected: true,
    status: 'ready',
    width: 800,
    height: 800,
  }
}

describe('ImagePoolPanel', () => {
  it('默认不选择商品素材，只提交本次明确勾选的图片', async () => {
    const wrapper = mount(ImagePoolPanel, {
      attachTo: document.body,
      props: {
        images: [image('img-1'), image('img-2')],
        loading: false,
        showTranslateAction: true,
      },
      global: { stubs: { Teleport: true } },
    })
    const translateButton = wrapper.findAll('button').find((button) => button.text().includes('AI 翻译/重绘'))
    const checkboxes = wrapper.findAll('input[aria-label^="选择处理图片"]')

    expect(translateButton?.attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('已选择 0 张，本次仅处理这些图片')
    expect(checkboxes.every((checkbox) => !(checkbox.element as HTMLInputElement).checked)).toBe(true)

    await checkboxes[1].setValue(true)
    expect(wrapper.text()).toContain('已选择 1 张，本次仅处理这些图片')

    await translateButton?.trigger('click')
    await flushPromises()
    expect(wrapper.emitted('translate')).toEqual([[['img-2']]])
    expect(wrapper.emitted('toggleDraftImage')).toBeUndefined()
  })

})
