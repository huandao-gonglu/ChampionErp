// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { reactive } from 'vue'
import { describe, expect, it } from 'vitest'
import SkuImagePicker from '../SkuImagePicker.vue'
import ProductImageEditorPanel from '../ProductImageEditorPanel.vue'
import ImagePoolPanel from '../ImagePoolPanel.vue'
import { createEmptyDraftDetail, createEmptyProduct } from '@/constants/initialState'
import type { ImageAsset } from '@/types/workflow'

const images: ImageAsset[] = ['original', 'edited'].map(id => ({ id, url: `https://cdn.test/${id}.jpg`, previewUrl: '', path: '', origin: 'source', usage: 'detail', platforms: [], selected: false, isMain: false, status: 'ready', width: 800, height: 800 }))

function productFixture() {
  const product = reactive(createEmptyProduct())
  product.skuItems = [{ id: 'red', name: '红色', source_sku_id: 'supplier-red', options: {}, cost_cny: '10', supplier_stock: '100', image_asset_id: 'original', barcode: '', package_dimensions: {}, active: true, source_snapshot: {} }]
  product.source.imagePool = images
  return product
}

describe('SKU 图片资产选择', () => {
  it('显示缩略图并提交资产 ID，不提交分发地址', async () => {
    const wrapper = mount(SkuImagePicker, { props: { modelValue: 'original', images }, global: { stubs: { Teleport: true } } })
    expect(wrapper.get('img').attributes('src')).toBe(images[0]!.url)
    await wrapper.get('button[aria-label="选择SKU 图片"]').trigger('click')
    await wrapper.get('button[aria-label="使用图片 edited"]').trigger('click')
    expect(wrapper.emitted('update:modelValue')).toEqual([['edited']])
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
  })

  it('图片已删除时明确提示重新选择，空池提示上传', async () => {
    const wrapper = mount(SkuImagePicker, { props: { modelValue: 'missing', images: [] }, global: { stubs: { Teleport: true } } })
    expect(wrapper.text()).toContain('关联图片已移出图片池')
    await wrapper.get('button').trigger('click')
    expect(wrapper.text()).toContain('图片池为空')
  })

  it('商品图片页关联直接更新商品默认图并触发保存', async () => {
    const product = productFixture()
    const wrapper = mount(ProductImageEditorPanel, { props: { product, images, loading: false } })
    const pool = wrapper.getComponent(ImagePoolPanel)
    pool.vm.$emit('assignSkus', [{ assetId: 'edited', skuIds: ['red'], mode: 'assign' }])
    expect(product.skuItems[0]!.image_asset_id).toBe('edited')
    expect(wrapper.emitted('saveSkuImages')).toHaveLength(1)
    expect(product.skuItems[0]!.image_asset_id).toBe('edited')
  })

  it('草稿图片页关联与 SKU 页共用覆盖字段，隔离商品及公共图集', async () => {
    const product = productFixture()
    const draft = reactive(createEmptyDraftDetail('ozon'))
    draft.skuItems = [{ sku_id: 'red', selected: true, sku: 'RED', stock: '1', overrides: {}, attributes_by_target: {}, pricing: { applied: true }, publications: {} }]
    const wrapper = mount(ProductImageEditorPanel, { props: { product, draft, images, loading: false } })
    wrapper.getComponent(ImagePoolPanel).vm.$emit('assignSkus', [{ assetId: 'edited', skuIds: ['red'], mode: 'assign' }])
    expect(draft.skuItems[0]!.overrides.image_asset_id).toBe('edited')
    expect(draft.skuItems[0]!.pricing.applied).toBe(true)
    expect(product.skuItems[0]!.image_asset_id).toBe('original')
    expect(draft.images).toEqual([])
    expect(wrapper.emitted('saveSkuImages')).toBeUndefined()
    wrapper.getComponent(ImagePoolPanel).vm.$emit('assignSkus', [{ assetId: '', skuIds: ['red'], mode: 'inherit' }])
    expect(draft.skuItems[0]!.overrides).not.toHaveProperty('image_asset_id')
  })
})
