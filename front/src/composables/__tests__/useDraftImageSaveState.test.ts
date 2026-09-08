import { effectScope, ref } from 'vue'
import { describe, expect, it, vi } from 'vitest'
import { createEmptyDraftDetail } from '@/constants/initialState'
import { useDraftImageSaveState } from '../useDraftImageSaveState'
describe('草稿图片保存状态', () => {
  it('记录修改、失败可重试、保存成功及重新加载后的状态', async () => {
    const draft = ref(createEmptyDraftDetail())
    const persist = vi.fn().mockResolvedValueOnce(false).mockResolvedValueOnce(true)
    const scope = effectScope()
    const state = scope.run(() => useDraftImageSaveState(draft, persist))!
    expect(state.dirty.value).toBe(false)
    draft.value.images.push({ assetId: 'image', role: 'main', order: 0 })
    expect(state.status.value).toBe('图片设置尚未保存')
    expect(await state.save()).toBe(false)
    expect(state.dirty.value).toBe(true)
    expect(state.status.value).toContain('保存失败')
    expect(await state.save()).toBe(true)
    expect(state.status.value).toBe('图片设置已保存')
    draft.value.images = []
    expect(state.dirty.value).toBe(true)
    draft.value = createEmptyDraftDetail()
    expect(state.dirty.value).toBe(false)
    scope.stop()
  })
  it('SKU 覆盖参与未保存判断，重复点击只发出一次保存', async () => {
    const draft = ref(createEmptyDraftDetail())
    draft.value.skuItems = [{ sku_id: 'red', overrides: {} } as typeof draft.value.skuItems[number]]
    let complete!: (ok: boolean) => void
    const persist = vi.fn(() => new Promise<boolean>(resolve => { complete = resolve }))
    const scope = effectScope()
    const state = scope.run(() => useDraftImageSaveState(draft, persist))!
    draft.value.skuItems[0].overrides.image_asset_id = 'red'
    expect(state.dirty.value).toBe(true)
    const pending = state.save()
    expect(state.status.value).toBe('正在保存…')
    expect(await state.save()).toBe(false)
    complete(true)
    await pending
    expect(persist).toHaveBeenCalledTimes(1)
    expect(state.dirty.value).toBe(false)
    scope.stop()
  })
})
