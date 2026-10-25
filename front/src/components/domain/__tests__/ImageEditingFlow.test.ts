import { enableAutoUnmount, flushPromises, mount, type DOMWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import { reactive } from 'vue'
import ProductImageEditorPanel from '../ProductImageEditorPanel.vue'
import ImagePoolPanel from '../ImagePoolPanel.vue'
import SkuImageAssignmentDialog from '../SkuImageAssignmentDialog.vue'
import { createEmptyDraftDetail, createEmptyProduct } from '@/constants/initialState'
import type { ImageAsset } from '@/types/workflow'

enableAutoUnmount(afterEach)
const images = ['red', 'pink', 'common'].map(id => ({ id, url: `/images/${id}.jpg`, previewUrl: '', path: '', origin: 'source', usage: 'detail', platforms: [], selected: false, isMain: false, status: 'ready', width: 800, height: 800 } as ImageAsset))
function fixture() {
  const product = reactive(createEmptyProduct())
  product.skuItems = ['red', 'pink'].map(id => ({ id, name: id === 'red' ? '红色' : '樱花粉', image_asset_id: id, active: true } as typeof product.skuItems[number]))
  const draft = reactive(createEmptyDraftDetail())
  draft.images = [{ assetId: 'common', role: 'main', order: 0 }, { assetId: 'pink', role: 'detail', order: 1 }]
  draft.skuItems = product.skuItems.map(sku => ({ sku_id: sku.id, selected: true, overrides: {}, pricing: { applied: true }, sku: sku.id, stock: '20', attributes_by_target: {}, publications: {} }))
  return { product, draft }
}
function button(wrapper: { findAll(selector: string): DOMWrapper<Element>[] }, text: string) { return wrapper.findAll('button').find(item => item.text() === text)! }

describe('图片编辑交互', () => {
  it('取消原关联显式使用公共图集，商品批量只保存一次', async () => {
    const { product } = fixture()
    const wrapper = mount(ProductImageEditorPanel, { props: { product, images, loading: false }, global: { stubs: { Teleport: true } } })
    const pool = wrapper.getComponent(ImagePoolPanel)
    await button(pool.get('[data-image-id="red"]'), '设置 SKU 主图（1）').trigger('click')
    await flushPromises()
    const dialog = wrapper.getComponent(SkuImageAssignmentDialog)
    await dialog.get('input[aria-label="选择 SKU 红色"]').setValue(false)
    await dialog.get('input[aria-label="选择 SKU 樱花粉"]').setValue(true)
    await button(dialog, '应用 SKU 图片设置').trigger('click')
    expect(product.skuItems.map(sku => sku.image_asset_id)).toEqual(['', 'red'])
    expect(wrapper.emitted('saveSkuImages')).toHaveLength(1)
  })
  it('商品素材删除先展示共享范围，确认后才提交删除', async () => {
    const wrapper = mount(ImagePoolPanel, { props: { images, loading: false }, global: { stubs: { Teleport: true } } })
    await wrapper.get('input[aria-label="选择处理图片 red"]').setValue(true)
    await button(wrapper, '从素材库删除选中').trigger('click')
    await flushPromises()
    expect(wrapper.emitted('delete')).toBeUndefined()
    expect(wrapper.get('dialog[open]').text()).toContain('其他草稿也会受影响')
    await button(wrapper, '确认删除共享素材').trigger('click')
    expect(wrapper.emitted('delete')).toEqual([[['red']]])
  })
})
