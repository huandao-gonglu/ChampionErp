import { effectScope, nextTick, ref } from 'vue'
import { flushPromises } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createEmptyDraftDetail, createEmptyDraftProductContext, createEmptyProduct } from '@/constants/initialState'
import { fetchDraftImageState, generateDraftImageSet, planDraftImageSet, processDraftImages, type DraftImageState, type ImageSetPlan } from '@/api/draftImages'
import { saveDraft, uploadImages } from '@/api/workflow/catalog'
import type { DraftMutationResponse } from '@/api/workflow/normalizers'
import type { ImageBatchSnapshot } from '@/api/imageBatches'
import { useDraftImageWorkbench } from '../useDraftImageWorkbench'
import { setOperationAvailability } from '@/utils/desktop'

vi.mock('@/api/draftImages', () => ({ fetchDraftImageState: vi.fn(), processDraftImages: vi.fn(), planDraftImageSet: vi.fn(), generateDraftImageSet: vi.fn() }))
vi.mock('@/api/workflow/catalog', () => ({ saveDraft: vi.fn(), uploadImages: vi.fn() }))
const clone = <T>(value: T): T => JSON.parse(JSON.stringify(value))
const scopes: ReturnType<typeof effectScope>[] = []
function fixture() {
  const draft = ref(createEmptyDraftDetail())
  draft.value.draftId = 'd'; draft.value.productId = 'p'; draft.value.updatedAt = 'v1'
  draft.value.images = [{ assetId: 'common', role: 'main', order: 0 }, { assetId: 'shared', role: 'detail', order: 1 }]
  draft.value.skuItems = [{ sku_id: 'red', selected: true, overrides: { image_asset_id: 'shared' }, pricing: { applied: true }, sku: 'R', stock: '2', attributes_by_target: {}, publications: {} }]
  const product = ref(createEmptyProduct())
  product.value.productId = 'p'
  const productContext = ref({ ...createEmptyDraftProductContext(), productId: 'p' })
  const active = ref(true)
  const response = (value = draft.value, batch: ImageBatchSnapshot | null = null): DraftImageState => ({
    draft: clone(value), product: clone(product.value), productContext: undefined,
    imagePool: clone(product.value.source.imagePool), productsIndex: [], draftsIndex: [], ok: true, batch,
  }) as unknown as DraftImageState
  vi.mocked(fetchDraftImageState).mockImplementation(async () => response())
  vi.mocked(saveDraft).mockImplementation(async next => ({ ...response(next), draft: { ...clone(next), updatedAt: 'saved' } } as DraftMutationResponse))
  const save = vi.fn(async () => true)
  const scope = effectScope(); scopes.push(scope)
  const work = scope.run(() => useDraftImageWorkbench({ draft, productContext, active, save,
    accept: result => { if (result.draft) draft.value = result.draft; if ('product' in result) product.value = result.product },
  }))!
  return { draft, product, active, response, work, save }
}
function batch(terminal = false): ImageBatchSnapshot {
  return { conversation_id: 'batch', draft_id: 'd', product_id: 'p', prompt: '修改', created_at: '', updated_at: '',
    total: 2, queued: 0, running: terminal ? 0 : 2, success: terminal ? 1 : 0, failed: terminal ? 1 : 0,
    completed: terminal ? 2 : 0, terminal,
    items: ['common', 'shared'].map((id, index) => ({ index, source_id: id, source_url: '', status: terminal ? index ? 'failed' : 'success' : 'running',
      error: terminal && index ? '服务繁忙' : '', outcome_unknown: false, result_urls: terminal && !index ? ['/result.png'] : [], started_at: '', finished_at: '' })),
  }
}
beforeEach(() => {
  vi.useFakeTimers(); vi.resetAllMocks()
  const values = new Map<string, string>()
  vi.stubGlobal('localStorage', { getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value), removeItem: (key: string) => values.delete(key), clear: () => values.clear() })
})
afterEach(() => { scopes.forEach(scope => scope.stop()); scopes.length = 0; vi.clearAllTimers(); vi.useRealTimers(); vi.unstubAllGlobals() })

describe('图片工作台操作与恢复', () => {
  it('当前端不能修图时可读原任务，翻译、编辑和重做均不保存草稿或创建批次', async () => {
    setOperationAvailability({ desktop_use_cases: [], desktop_reason: '需要桌面端', blocked_use_cases: { 'image.translate': 'Gemini Canvas 需要桌面端' } })
    const { work, save } = fixture()
    await flushPromises()
    await work.start('translate', ['common'])
    await work.start('edit', ['common'], '修图')
    await work.generateSet(undefined, 'v1', ['page-1'])
    expect(work.notice.value).toContain('Gemini Canvas')
    expect(fetchDraftImageState).toHaveBeenCalled()
    expect(save).not.toHaveBeenCalled()
    expect(processDraftImages).not.toHaveBeenCalled()
    expect(generateDraftImageSet).not.toHaveBeenCalled()
  })
  it('已确认的翻译遇到版本变化或已有其他批次时停止，并说明原因', async () => {
    window.championDesktop = { localOperations: true, openLicenses: vi.fn() }
    try {
      const { work, save, draft } = fixture()
      await flushPromises()
      await work.resumeTranslation(['common'], 'draft_images_web', 'v0')
      expect(work.notice.value).toContain('草稿已更新')
      work.batch.value = batch()
      await work.resumeTranslation(['common'], 'draft_images_web', draft.value.updatedAt)
      expect(work.notice.value).toContain('已有操作进行中')
      expect(save).not.toHaveBeenCalled()
      expect(processDraftImages).not.toHaveBeenCalled()
    } finally { delete window.championDesktop }
  })
  it('只生成 SKU 图可独立提交，空选择不会创建方案', async () => {
    const { work, draft, save } = fixture()
    await flushPromises()
    await work.planSet([], undefined, '', [])
    expect(save).not.toHaveBeenCalled()
    await work.planSet([], undefined, '统一光线', ['red'])
    expect(save).toHaveBeenCalledOnce()
    expect(planDraftImageSet).toHaveBeenCalledWith(draft.value, [], undefined, '统一光线', ['red'])
  })
  it('套图先保存后规划，生成完成仍保留旧引用；采用与撤销只修改共用图', async () => {
    const { work, draft, response, save } = fixture()
    await flushPromises()
    const plan: ImageSetPlan = { style: '简洁留白', pages: [
      { key: 'common', title: '主图', role: 'main', prompt: '商品展示', source_image_ids: ['common'] },
      { key: 'shared', title: '细节', role: 'detail', prompt: '材质展示', source_image_ids: ['shared'] },
    ] }
    vi.mocked(planDraftImageSet).mockResolvedValue({ ok: true, plan, updated_at: 'v1' })
    expect((await work.planSet(['common', 'shared'], 2, '明亮'))?.plan).toEqual(plan)
    expect(save).toHaveBeenCalledOnce()
    expect(planDraftImageSet).toHaveBeenCalledWith(draft.value, ['common', 'shared'], 2, '明亮', [])
    const active = { ...batch(), image_set: plan }
    vi.mocked(generateDraftImageSet).mockResolvedValue(response(draft.value, active))
    await work.generateSet(plan, 'v1')
    expect(work.busy.value).toBe(true)
    const completed = { ...batch(true), image_set: plan }
    completed.items[0].result_asset_ids = ['result']
    vi.mocked(fetchDraftImageState).mockResolvedValue(response(draft.value, completed))
    await vi.advanceTimersByTimeAsync(1000)
    expect(draft.value.images.map(image => image.assetId)).toEqual(['common', 'shared'])
    expect(work.undoLabel.value).toBe('')
    const skus = clone(draft.value.skuItems)
    await work.applySet([{ assetId: 'result', role: 'main', order: 0 }])
    expect(draft.value.images.map(image => image.assetId)).toEqual(['result'])
    expect(draft.value.skuItems).toEqual(skus)
    await work.undo()
    expect(draft.value.images.map(image => image.assetId)).toEqual(['common', 'shared'])
    expect(draft.value.skuItems).toEqual(skus)
    expect(generateDraftImageSet).toHaveBeenCalledTimes(1)
    await work.applySet([{ assetId: 'foreign', role: 'main', order: 0 }])
    expect(draft.value.images.map(image => image.assetId)).toEqual(['common', 'shared'])
  })
  it('套图提交超时只恢复原任务，单张重做携带原批次与页面', async () => {
    const { work, draft, response } = fixture()
    await flushPromises()
    const plan: ImageSetPlan = { style: '简洁', pages: [
      { key: 'main', title: '主图', role: 'main', prompt: '主图', source_image_ids: ['common'] },
      { key: 'detail', title: '细节', role: 'detail', prompt: '细节', source_image_ids: ['shared'] },
    ] }
    vi.mocked(generateDraftImageSet).mockRejectedValueOnce(new Error('timeout'))
    await work.generateSet(plan)
    const requestId = localStorage.getItem('draft-image-request:d')
    expect(requestId).toBeTruthy()
    expect(work.busy.value).toBe(true)
    vi.mocked(fetchDraftImageState).mockResolvedValue(response(draft.value, { ...batch(true), image_set: plan, conversation_id: requestId! }))
    await vi.advanceTimersByTimeAsync(1000)
    vi.mocked(generateDraftImageSet).mockResolvedValue(response(draft.value, { ...batch(), image_set: plan }))
    await work.generateSet(undefined, draft.value.updatedAt, ['detail'])
    expect(generateDraftImageSet).toHaveBeenLastCalledWith(draft.value, expect.any(String), {
      updatedAt: draft.value.updatedAt, plan: undefined, baseRequestId: requestId, retryPageIds: ['detail'],
    })
    expect(generateDraftImageSet).toHaveBeenCalledTimes(2)
  })
  it('共用与 SKU 图同次采用并撤销；失败规格保留原图，SKU 结果不能混入共用图', async () => {
    const { work, draft } = fixture()
    await flushPromises()
    draft.value.skuItems.push({ ...clone(draft.value.skuItems[0]), sku_id: 'pink', overrides: { image_asset_id: 'pink-original' } })
    const initial = clone(draft.value)
    const plan: ImageSetPlan = { style: '统一风格', pages: [
      { key: 'common', title: '共用主图', role: 'main', prompt: '主图', source_image_ids: ['common'] },
      { key: 'shared', title: '共用细节', role: 'detail', prompt: '细节', source_image_ids: ['shared'] },
      { key: 'red-page', title: '红色款', role: 'main', prompt: '红色款主图', source_image_ids: ['shared'], scope: 'sku', sku_ids: ['red'] },
      { key: 'pink-page', title: '粉色款', role: 'main', prompt: '粉色款主图', source_image_ids: ['pink-original'], scope: 'sku', sku_ids: ['pink'] },
    ] }
    const completed = { ...batch(true), image_set: plan }
    completed.items[0].result_asset_ids = ['common-result']
    completed.items.push({ ...completed.items[0], index: 2, source_id: 'red-page', result_asset_ids: ['red-result'] },
      { ...completed.items[1], index: 3, source_id: 'pink-page' })
    work.batch.value = completed
    await work.applySet([{ assetId: 'red-result', role: 'main', order: 0 }])
    expect(saveDraft).not.toHaveBeenCalled()
    await work.applySet([{ assetId: 'common-result', role: 'main', order: 0 }], ['red-page'])
    expect(saveDraft).toHaveBeenCalledTimes(1)
    expect(draft.value.images.map(ref => ref.assetId)).toEqual(['common-result'])
    expect(draft.value.skuItems.map(row => row.overrides.image_asset_id)).toEqual(['red-result', 'pink-original'])
    expect(draft.value.skuItems[0].pricing).toEqual(initial.skuItems[0].pricing)
    await work.undo()
    expect(draft.value.images).toEqual(initial.images)
    expect(draft.value.skuItems).toEqual(initial.skuItems)
    await work.redo()
    expect(draft.value.skuItems[0].overrides.image_asset_id).toBe('red-result')
    expect(generateDraftImageSet).not.toHaveBeenCalled()
    await work.undo()
    await work.applySet([], ['red-page'])
    expect(draft.value.images).toEqual(initial.images)
    expect(draft.value.skuItems[0].overrides.image_asset_id).toBe('red-result')
    const saves = vi.mocked(saveDraft).mock.calls.length
    await work.applySet([], ['pink-page'])
    expect(saveDraft).toHaveBeenCalledTimes(saves)
  })
  it('移出仅保存共用引用，撤销重做不改 SKU、商品或核价；新操作清空重做', async () => {
    const { work, draft, product } = fixture()
    await flushPromises()
    const beforeProduct = clone(product.value)
    await work.remove(['shared'])
    expect(draft.value.images.map(image => image.assetId)).toEqual(['common'])
    expect(draft.value.skuItems[0].overrides.image_asset_id).toBe('shared')
    expect(work.undoLabel.value).toBe('移出 1 张共用图')
    await work.undo()
    expect(draft.value.images.map(image => image.assetId)).toEqual(['common', 'shared'])
    await work.redo()
    expect(draft.value.images.map(image => image.assetId)).toEqual(['common'])
    await work.undo()
    await work.reorder(['shared', 'common'])
    expect(draft.value.images).toEqual([{ assetId: 'shared', role: 'main', order: 0 }, { assetId: 'common', role: 'detail', order: 1 }])
    expect(work.redoLabel.value).toBe('')
    expect(draft.value.skuItems[0].pricing.applied).toBe(true)
    expect(product.value).toEqual(beforeProduct)
    expect(processDraftImages).not.toHaveBeenCalled()
  })
  it('一次部分成功算一步；重做恢复已生成引用，二次处理发送当前图片', async () => {
    const { work, draft, response } = fixture()
    await flushPromises()
    vi.mocked(processDraftImages).mockResolvedValue(response(draft.value, batch()))
    await work.start('edit', ['common', 'shared'], '修改标题')
    expect(work.busy.value).toBe(true)
    await work.start('edit', ['shared'], '不应提交')
    await work.remove(['shared'])
    expect(processDraftImages).toHaveBeenCalledTimes(1)
    const updated = clone(draft.value)
    updated.images[0].assetId = 'result'
    vi.mocked(fetchDraftImageState).mockResolvedValue(response(updated, batch(true)))
    await vi.advanceTimersByTimeAsync(1000)
    expect(work.busy.value).toBe(false)
    expect(draft.value.images.map(image => image.assetId)).toEqual(['result', 'shared'])
    expect(work.undoLabel.value).toBe('AI 修改 2 张')
    await work.undo()
    expect(draft.value.images.map(image => image.assetId)).toEqual(['common', 'shared'])
    await work.redo()
    expect(draft.value.images.map(image => image.assetId)).toEqual(['result', 'shared'])
    expect(processDraftImages).toHaveBeenCalledTimes(1)
    vi.mocked(processDraftImages).mockResolvedValue(response(draft.value, { ...batch(), conversation_id: 'second' }))
    await work.start('edit', ['result'], '再次修改')
    expect(vi.mocked(processDraftImages).mock.calls[1][2]).toEqual(['result'])
    expect(work.redoLabel.value).toBe('')
  })
  it('超时与查询失败保持原任务，不重新提交；刷新按草稿恢复查询', async () => {
    const { work, draft, response, active } = fixture()
    await flushPromises()
    vi.mocked(processDraftImages).mockRejectedValue(new Error('timeout'))
    await work.start('translate', ['common'])
    expect(work.busy.value).toBe(true)
    expect(work.notice.value).toContain('尚未确认')
    const requestId = localStorage.getItem('draft-image-request:d')
    expect(requestId).toBeTruthy()
    vi.mocked(fetchDraftImageState).mockRejectedValueOnce(new Error('offline'))
    await vi.advanceTimersByTimeAsync(1000)
    expect(work.busy.value).toBe(true)
    expect(work.notice.value).toContain('重新连接')
    active.value = false; await nextTick()
    vi.mocked(fetchDraftImageState).mockResolvedValue(response(draft.value, { ...batch(), conversation_id: requestId! }))
    active.value = true; await flushPromises()
    expect(fetchDraftImageState).toHaveBeenLastCalledWith('d', requestId, draft.value.updatedAt)
    expect(work.busy.value).toBe(true)
    vi.mocked(fetchDraftImageState).mockResolvedValue(response(draft.value, { ...batch(true), conversation_id: requestId! }))
    await vi.advanceTimersByTimeAsync(2000)
    expect(work.busy.value).toBe(false)
    expect(localStorage.getItem('draft-image-request:d')).toBeNull()
    expect(processDraftImages).toHaveBeenCalledTimes(1)
    expect(work.undoLabel.value).toBe('') // 重新打开的历史边界不冒充旧操作可撤销。
  })
  it('全部失败不把外部引用变化记录成本批次可撤销的结果', async () => {
    const { work, draft, response } = fixture()
    await flushPromises()
    vi.mocked(processDraftImages).mockResolvedValue(response(draft.value, batch()))
    await work.start('edit', ['common'], '修改')
    const external = clone(draft.value); external.images[0].assetId = 'external'
    const failed = batch(true); failed.success = 0; failed.failed = 2
    failed.items.forEach(item => { item.status = 'failed'; item.result_urls = [] })
    vi.mocked(fetchDraftImageState).mockResolvedValue(response(external, failed))
    await vi.advanceTimersByTimeAsync(1000)
    expect(work.undoLabel.value).toBe('')
    expect(draft.value.images[0].assetId).toBe('external')
  })
  it('预检失败后展示原因并恢复手动提交', async () => {
    const { work, response, active } = fixture()
    await flushPromises()
    active.value = false; await nextTick()
    const failed = batch(true)
    Object.assign(failed, { failed: 2, success: 0 })
    for (const item of failed.items) Object.assign(item, { status: 'failed', error: '未检测到 Canvas 应用预览', result_urls: [] })
    vi.mocked(fetchDraftImageState).mockResolvedValue(response(undefined, failed))
    active.value = true; await flushPromises()
    expect(work.notice.value).toContain('2 张尚未执行。未检测到 Canvas 应用预览')
    expect(work.busy.value).toBe(false)
    expect(processDraftImages).not.toHaveBeenCalled()
  })
  it('终态含未知结果时释放草稿，刷新不重发，手动选择后可重新翻译', async () => {
    const { work, response, active } = fixture()
    await flushPromises()
    active.value = false; await nextTick()
    localStorage.setItem('draft-image-request:d', 'batch')
    const unknown = batch(true); unknown.items[1].outcome_unknown = true
    vi.mocked(fetchDraftImageState).mockResolvedValue(response(undefined, unknown))
    active.value = true; await flushPromises()
    expect(work.busy.value).toBe(false)
    expect(work.notice.value).toContain('待核对')
    expect(localStorage.getItem('draft-image-request:d')).toBeNull()
    vi.mocked(fetchDraftImageState).mockClear()
    await vi.advanceTimersByTimeAsync(5000)
    expect(fetchDraftImageState).not.toHaveBeenCalled()
    expect(processDraftImages).not.toHaveBeenCalled()
    vi.mocked(processDraftImages).mockResolvedValue(response(undefined, batch()))
    await work.start('translate', ['shared'])
    expect(processDraftImages).toHaveBeenCalledOnce()
    expect(work.busy.value).toBe(true)
  })
  it('草稿切换使旧异步响应失效并清空历史', async () => {
    const { work, draft, response } = fixture()
    await flushPromises()
    await work.remove(['shared'])
    let resolve!: (value: DraftImageState) => void
    vi.mocked(processDraftImages).mockImplementation(() => new Promise(done => { resolve = done }))
    const running = work.start('edit', ['common'], '修改')
    await flushPromises()
    const old = response(draft.value, batch())
    draft.value = { ...clone(draft.value), draftId: 'another', images: [] }
    await flushPromises()
    resolve(old); await running
    expect(draft.value.draftId).toBe('another')
    expect(draft.value.images).toEqual([])
    expect(work.undoLabel.value).toBe('')
  })
  it('撤销保存失败不误报成功；回执丢失时以重新读取结果为准', async () => {
    const { work, draft, response } = fixture()
    await flushPromises()
    await work.remove(['shared'])
    const saved = clone(draft.value)
    vi.mocked(saveDraft).mockRejectedValueOnce(new Error('保存超时'))
    vi.mocked(fetchDraftImageState).mockResolvedValue(response(saved))
    await work.undo()
    expect(draft.value.images.map(image => image.assetId)).toEqual(['common'])
    expect(work.notice.value).toContain('已重新读取')
    expect(work.notice.value).not.toContain('已撤销')
    expect(work.undoLabel.value).toBe('')
    expect(work.redoLabel.value).toBe('')
  })
  it('添加共用图片可撤销，刷新/重新打开不会保留误报的撤销状态', async () => {
    const { work, draft, active } = fixture()
    await flushPromises()
    await work.add(['new'])
    expect(draft.value.images.at(-1)?.assetId).toBe('new')
    await work.undo()
    expect(draft.value.images.map(image => image.assetId)).toEqual(['common', 'shared'])
    await work.redo()
    active.value = false; await nextTick(); active.value = true; await flushPromises()
    expect(work.undoLabel.value).toBe('')
    expect(draft.value.images.at(-1)?.assetId).toBe('new')
  })
  it('上传只加入本次回执的资产，不把另一任务新增的图片混入共用图', async () => {
    vi.useRealTimers()
    const { work, draft, response } = fixture()
    await flushPromises()
    vi.mocked(uploadImages).mockResolvedValue({ ...response(), raw: { uploaded_image_ids: ['uploaded'] },
      imagePool: [{ id: 'uploaded' }, { id: 'another-draft-result' }] as DraftImageState['imagePool'] })
    await work.upload([new File(['test'], 'example.png', { type: 'image/png' })])
    expect(draft.value.images.map(image => image.assetId)).toEqual(['common', 'shared', 'uploaded'])
  })
  it('保存前置失败时不提交图片任务', async () => {
    const { work, save } = fixture()
    await flushPromises()
    save.mockResolvedValue(false)
    await work.start('translate', ['common'])
    expect(processDraftImages).not.toHaveBeenCalled()
    expect(work.notice.value).toContain('尚未开始处理')
  })
})
