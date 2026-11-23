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
    setOperationAvailability({ delegated_use_cases: [], desktop_use_cases: ['image.translate'], desktop_reason: '需要本机', blocked_use_cases: {} })
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
    setOperationAvailability({ delegated_use_cases: [], desktop_use_cases: [], desktop_reason: '', blocked_use_cases: {} })
    desktop.vm.restoreImageAction(state)
    await flushPromises()
    await desktop.get('button[type="submit"]').trigger('click')
    expect(desktop.emitted('imageEdit')).toEqual([[{ imageIds: ['b'], prompt: '保留颜色' }]])
  })

  it('默认不勾选商品素材，翻译只提交本次选中的图片', async () => {
    setOperationAvailability({ delegated_use_cases: [], desktop_use_cases: [], desktop_reason: '', blocked_use_cases: {} })
    const wrapper = mount(ImagePoolPanel, {
      attachTo: document.body,
      props: { images: [image('a'), image('b')], loading: false, showTranslateAction: true },
      global: { stubs: { Teleport: true } },
    })
    const translate = wrapper.findAll<HTMLButtonElement>('button').find(button => button.text().startsWith('AI 翻译/重绘'))!
    const checkboxes = wrapper.findAll<HTMLInputElement>('input[aria-label^="选择处理图片"]')
    expect(translate.element.disabled).toBe(true)
    expect(checkboxes.map(({ element }) => element.checked)).toEqual([false, false])

    await wrapper.get<HTMLInputElement>('[aria-label="选择处理图片 b"]').setValue(true)
    expect(translate.element.disabled).toBe(false)
    await translate.trigger('click')

    expect(wrapper.emitted('translate')).toEqual([[['b']]])
  })
})
