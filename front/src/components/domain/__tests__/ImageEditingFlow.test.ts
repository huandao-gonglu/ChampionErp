import { enableAutoUnmount, flushPromises, mount, type DOMWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import { reactive } from 'vue'
import ProductImageEditorPanel from '../ProductImageEditorPanel.vue'
import ImagePoolPanel from '../ImagePoolPanel.vue'
import SkuImageAssignmentDialog from '../SkuImageAssignmentDialog.vue'
import DraftImageRefPanel from '../DraftImageRefPanel.vue'
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
  it('搜索后批量选中不会清空搜索外的原关联，草稿仅本地更新', async () => {
    const { product, draft } = fixture()
    const wrapper = mount(ProductImageEditorPanel, { props: { product, draft, images, loading: false }, global: { stubs: { Teleport: true } } })
    const pool = wrapper.getComponent(ImagePoolPanel)
    await button(pool.get('[data-image-id="red"]'), '设置 SKU 主图（1）').trigger('click')
    await flushPromises()
    const dialog = wrapper.getComponent(SkuImageAssignmentDialog)
    await dialog.get('input[placeholder="输入款式、颜色或尺码"]').setValue('粉')
    await button(dialog, '选中搜索结果').trigger('click')
    await button(dialog, '应用 SKU 图片设置').trigger('click')
    expect(draft.skuItems[0].overrides).toEqual({})
    expect(draft.skuItems[1].overrides.image_asset_id).toBe('red')
    expect(draft.skuItems.every(sku => sku.pricing.applied)).toBe(true)
    expect(product.skuItems.map(sku => sku.image_asset_id)).toEqual(['red', 'pink'])
    expect(wrapper.emitted('saveSkuImages')).toBeUndefined()
  })
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
  it('设主图与排序同步；公共图集移除不删除商品素材', async () => {
    const { draft } = fixture()
    const wrapper = mount(DraftImageRefPanel, { props: { draft, images, loading: false } })
    await button(wrapper.get('[data-draft-image-id="pink"]'), '设为公共主图').trigger('click')
    expect(draft.images).toEqual([{ assetId: 'pink', role: 'main', order: 0 }, { assetId: 'common', role: 'detail', order: 1 }])
    await button(wrapper.get('[data-draft-image-id="pink"]'), '从当前草稿移除').trigger('click')
    expect(draft.images).toEqual([{ assetId: 'common', role: 'main', order: 0 }])
    expect(images).toHaveLength(3)
  })
  it('筛选隐藏已选图片时明确提示，草稿不暴露素材库删除入口', async () => {
    const wrapper = mount(ImagePoolPanel, { props: { images, loading: false, showDraftControls: true } })
    await wrapper.get('input[aria-label="选择处理图片 red"]').setValue(true)
    await wrapper.get('input[placeholder="搜索款式、颜色、用途"]').setValue('pink')
    expect(wrapper.text()).toContain('包含筛选外的已选图片')
    expect(wrapper.findAll('[data-image-id]')).toHaveLength(1)
    expect(wrapper.find('details').exists()).toBe(false)
    expect(wrapper.findAll('button').some(item => item.text() === '清空商品素材库')).toBe(false)
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
