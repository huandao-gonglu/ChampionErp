import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ImagePoolPanel from '@/components/domain/ImagePoolPanel.vue'
import type { ImageAsset } from '@/types/workflow'
import { setOperationAvailability } from '@/utils/desktop'
import { launchDesktop } from '@/utils/desktopHandoff'
vi.mock('@/utils/desktopHandoff', () => ({ launchDesktop: vi.fn() }))
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
  it('网页填写修图要求，确认生成才打开桌面并可恢复所选素材和要求', async () => {
    setOperationAvailability({ desktop_use_cases: ['image.translate'], desktop_reason: '需要本机', blocked_use_cases: {} })
    const options = { attachTo: document.body, props: { images: [image('a'), image('b')], loading: false }, global: { stubs: { Teleport: true } } }
    const web = mount(ImagePoolPanel, options)
    await web.findAll('input[aria-label^="选择处理图片"]')[1].setValue(true)
    await web.findAll('button').find(button => button.text().includes('AI 图生图'))!.trigger('click')
    await web.get('textarea').setValue('保留颜色')
    expect(launchDesktop).not.toHaveBeenCalled()
    await web.get('button[type="submit"]').trigger('click')
    expect(launchDesktop).toHaveBeenCalledOnce()
    expect(web.emitted('imageEdit')).toBeUndefined()
    const state = web.vm.pendingImageAction()!
    const desktop = mount(ImagePoolPanel, options)
    setOperationAvailability({ desktop_use_cases: [], desktop_reason: '', blocked_use_cases: {} })
    desktop.vm.restoreImageAction(state)
    await flushPromises()
    await desktop.get('button[type="submit"]').trigger('click')
    expect(desktop.emitted('imageEdit')).toEqual([[{ imageIds: ['b'], prompt: '保留颜色' }]])
  })

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
