import type { DraftDetail, DraftImageRef, ImageAsset, ProductSku } from '@/types/workflow'

export const imageRoles = [
  { value: 'main', label: '主图' }, { value: 'detail', label: '详情' },
  { value: 'size', label: '尺寸' }, { value: 'scene', label: '场景' },
  { value: 'package', label: '包装' }, { value: 'selling_point', label: '卖点' },
  { value: 'material', label: '材质' }, { value: 'other', label: '其他' },
] as const
export function imageSourceLabel(image: ImageAsset) {
  if (image.origin === 'ai_generated') return 'AI 处理'
  if (['upload', 'local_upload'].includes(image.origin)) return '上传图片'
  return '采集图片'
}
export function imageUsageLabel(image: ImageAsset) { return imageRoles.find(role => role.value === image.usage)?.label || '其他' }
export function imageUrl(image: ImageAsset) { return image.previewUrl || image.url || image.path }
export interface SkuImageAssignment {
  skuId: string; name: string; imageAssetId: string; inherited: boolean; selected: boolean
}
export interface SkuImageBatchChange { assetId: string; skuIds: string[]; mode: 'assign' | 'inherit' | 'public' }
export function skuImageAssignments(skus: ProductSku[], draft?: DraftDetail): SkuImageAssignment[] {
  return skus.filter(sku => sku.active).map(sku => {
    const row = draft?.skuItems.find(item => item.sku_id === sku.id)
    return { skuId: sku.id, name: sku.name, imageAssetId: String(row?.overrides.image_asset_id ?? sku.image_asset_id ?? ''),
      inherited: Boolean(draft && row?.overrides.image_asset_id == null), selected: draft ? Boolean(row?.selected) : true }
  })
}
export function draftImageSignature(draft: DraftDetail) {
  return JSON.stringify([draft.images, draft.skuItems.map(row => [row.sku_id, row.overrides.image_asset_id ?? null])])
}
export function skuPublishImages(draft: DraftDetail, assignment: SkuImageAssignment, images: ImageAsset[]): DraftImageRef[] {
  const refs = [...draft.images].sort((a, b) => a.order - b.order)
  // 与发布投影一致：SKU 图片在最前，公共图集去重，原公共主图成为详情图。
  if (assignment.imageAssetId && images.some(image => image.id === assignment.imageAssetId)) {
    return [{ assetId: assignment.imageAssetId, role: 'main', order: 0 }, ...refs.filter(ref => ref.assetId !== assignment.imageAssetId)
      .map((ref, index) => ({ ...ref, role: ref.role === 'main' ? 'detail' as const : ref.role, order: index + 1 }))]
  }
  if (assignment.imageAssetId) return [] // 关联丢失时发布会报错，不能伪装成已回退到公共图集。
  return refs.map((ref, order) => ({ ...ref, order }))
}
