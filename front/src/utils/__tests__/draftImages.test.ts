import { describe, expect, it } from 'vitest'
import { createEmptyDraftDetail, createEmptyProduct } from '@/constants/initialState'
import { skuImageAssignments, skuPublishImages } from '../draftImages'
import type { ImageAsset } from '@/types/workflow'
const images = ['red', 'common', 'detail'].map(id => ({ id }) as ImageAsset)
const assignment = { skuId: 'sku-red', name: '红色', imageAssetId: 'red', inherited: true, selected: true }
describe('SKU 发布图片组装', () => {
  it('SKU 主图在前并去重，公共图集和原对象保持不变', () => {
    const draft = createEmptyDraftDetail()
    draft.images = [{ assetId: 'common', role: 'main', order: 0 }, { assetId: 'red', role: 'detail', order: 1 }, { assetId: 'detail', role: 'size', order: 2 }]
    const before = JSON.stringify(draft)
    expect(skuPublishImages(draft, assignment, images)).toEqual([
      { assetId: 'red', role: 'main', order: 0 }, { assetId: 'common', role: 'detail', order: 1 }, { assetId: 'detail', role: 'size', order: 2 },
    ])
    expect(JSON.stringify(draft)).toBe(before)
  })
  it('无专属主图时沿用公共顺序；关联丢失时不能回退伪装成功', () => {
    const draft = createEmptyDraftDetail()
    draft.images = [{ assetId: 'detail', role: 'detail', order: 1 }, { assetId: 'common', role: 'main', order: 0 }]
    expect(skuPublishImages(draft, { ...assignment, imageAssetId: '' }, images).map(ref => ref.assetId)).toEqual(['common', 'detail'])
    expect(skuPublishImages(draft, { ...assignment, imageAssetId: 'missing' }, images)).toEqual([])
  })
  it('空覆盖明确使用公共图集，删除覆盖后才恢复继承', () => {
    const product = createEmptyProduct()
    product.skuItems = [{ id: 'red', name: '红色', image_asset_id: 'red', active: true } as typeof product.skuItems[number]]
    const draft = createEmptyDraftDetail()
    draft.skuItems = [{ sku_id: 'red', selected: true, overrides: { image_asset_id: '' }, sku: 'red', stock: '20', attributes_by_target: {}, pricing: {}, publications: {} }]
    expect(skuImageAssignments(product.skuItems, draft)[0]).toMatchObject({ imageAssetId: '', inherited: false, selected: true })
    delete draft.skuItems[0].overrides.image_asset_id
    expect(skuImageAssignments(product.skuItems, draft)[0]).toMatchObject({ imageAssetId: 'red', inherited: true })
  })
})
