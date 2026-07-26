import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import ImagePoolPanel from '@/components/domain/ImagePoolPanel.vue'
import type { ImageAsset } from '@/types/workflow'

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
  it('默认不选择草稿图片，只提交本次明确勾选的图片', async () => {
    const wrapper = mount(ImagePoolPanel, {
      props: {
        images: [image('img-1'), image('img-2')],
        loading: false,
        showTranslateAction: true,
        showDraftControls: true,
        draftAssetIds: ['img-1', 'img-2'],
      },
    })
    const translateButton = wrapper.findAll('button').find((button) => button.text().includes('AI 翻译/重绘'))
    const checkboxes = wrapper.findAll('input[type="checkbox"]')

    expect(translateButton?.attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('已选择 0 张，本次仅处理这些图片')
    expect(checkboxes.every((checkbox) => !(checkbox.element as HTMLInputElement).checked)).toBe(true)

    await checkboxes[1].setValue(true)
    expect(wrapper.text()).toContain('已选择 1 张，本次仅处理这些图片')

    await translateButton?.trigger('click')
    expect(wrapper.emitted('translate')).toEqual([[['img-2']]])
    expect(wrapper.emitted('toggleDraftImage')).toBeUndefined()
  })

  it('将发布图片归属与本次处理选择分开', async () => {
    const first = image('img-1')
    const wrapper = mount(ImagePoolPanel, {
      props: {
        images: [first],
        loading: false,
        showDraftControls: true,
        draftAssetIds: ['img-1'],
      },
    })
    const membershipButton = wrapper.findAll('button').find((button) => button.text() === '移出发布图片')

    await membershipButton?.trigger('click')
    expect(wrapper.emitted('toggleDraftImage')).toEqual([[first, false]])
    expect(wrapper.emitted('translate')).toBeUndefined()
  })
})
