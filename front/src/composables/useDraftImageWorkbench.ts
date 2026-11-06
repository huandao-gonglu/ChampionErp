import { computed, onScopeDispose, ref, watch, type Ref } from 'vue'
import { fetchDraftImageState, generateDraftImageSet, planDraftImageSet, processDraftImages, type DraftImageState, type ImageSetPlan } from '@/api/draftImages'
import { saveDraft, uploadImages } from '@/api/workflow/catalog'
import type { DraftMutationResponse, ProductMutationResponse } from '@/api/workflow/normalizers'
import type { ImageBatchSnapshot } from '@/api/imageBatches'
import type { DraftDetail, DraftImageRef, Product } from '@/types/workflow'
import { businessStorageKey } from '@/utils/accountTransport'

export interface ImageSnapshot { images: DraftImageRef[]; skus: Array<[string, string | null]> }
interface ImageOperation { label: string; before: ImageSnapshot; after: ImageSnapshot }
const clone = <T>(value: T): T => JSON.parse(JSON.stringify(value))
export function snapshotDraftImages(draft: DraftDetail): ImageSnapshot {
  return clone({ images: draft.images, skus: draft.skuItems.map(row => [row.sku_id, row.overrides.image_asset_id ?? null] as [string, string | null]) })
}
export function restoreDraftImages(draft: DraftDetail, snapshot: ImageSnapshot) {
  draft.images = clone(snapshot.images)
  for (const [id, asset] of snapshot.skus) {
    const row = draft.skuItems.find(row => row.sku_id === id)
    if (!row) continue
    if (asset === null) delete row.overrides.image_asset_id
    else row.overrides.image_asset_id = asset
  }
}
function signature(snapshot: ImageSnapshot) { return JSON.stringify(snapshot) }
function readFile(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result))
    reader.onerror = () => reject(new Error(`无法读取图片：${file.name}`))
    reader.readAsDataURL(file)
  })
}
export function useDraftImageWorkbench(options: {
  draft: Ref<DraftDetail>; product: Ref<Product>; active: Ref<boolean>
  save: () => Promise<boolean>
  accept: (result: DraftMutationResponse | ProductMutationResponse) => void
}) {
  const { draft, active } = options
  const batch = ref<ImageBatchSnapshot | null>(null)
  const checking = ref(true)
  const saving = ref(false)
  const pending = ref('')
  const notice = ref('')
  const past = ref<ImageOperation[]>([])
  const future = ref<ImageOperation[]>([])
  let epoch = 0
  let timer: ReturnType<typeof setTimeout> | undefined
  let processingBefore: ImageSnapshot | null = null
  let processingLabel = ''
  let acceptedSignature = ''
  let observedBatch = ''
  const unknown = computed(() => batch.value?.items.some(item => item.outcome_unknown) || false)
  const busy = computed(() => checking.value || saving.value || Boolean(pending.value) || Boolean(batch.value && (!batch.value.terminal || unknown.value)))
  const undoLabel = computed(() => past.value.at(-1)?.label || '')
  const redoLabel = computed(() => future.value.at(-1)?.label || '')
  const storagePrefix = businessStorageKey('draft-image-request')
  const key = (id: string) => `${storagePrefix}:${id}`
  function remember(id: string, value: string) {
    try { if (value) localStorage.setItem(key(id), value); else localStorage.removeItem(key(id)) } catch { /* 后端仍按草稿恢复任务。 */ }
  }
  function accept(result: DraftMutationResponse | ProductMutationResponse) {
    options.accept(result)
    acceptedSignature = signature(snapshotDraftImages(draft.value))
  }
  function record(label: string, before: ImageSnapshot) {
    const after = snapshotDraftImages(draft.value)
    if (signature(before) !== signature(after)) past.value.push({ label, before, after })
    future.value = []
  }
  function applyState(result: DraftImageState, force = false) {
    const next = result.batch
    if (!next && pending.value) {
      notice.value = '提交结果尚未确认，正在查询原任务…'
      return
    }
    const changed = next && (next.conversation_id !== observedBatch || !next.terminal || Boolean(pending.value))
    if (changed || force || checking.value) accept(result)
    batch.value = next
    if (!next) return
    observedBatch = next.conversation_id
    if (!next.terminal || next.items.some(item => item.outcome_unknown)) {
      notice.value = next.items.some(item => item.outcome_unknown) ? '处理结果待核对，请勿重复提交。' : ''
      return
    }
    if (processingBefore) {
      if (next.success > 0) record(processingLabel, processingBefore)
      else if (signature(processingBefore) !== signature(snapshotDraftImages(draft.value))) {
        // 全部失败时的引用变化来自外部，不能伪装成本批次的可撤销成果。
        past.value = []; future.value = []
      }
      processingBefore = null
    }
    pending.value = ''
    remember(draft.value.draftId, '')
    notice.value = ''
  }
  async function poll(token = epoch) {
    if (!active.value || token !== epoch) return
    try {
      const result = await fetchDraftImageState(draft.value.draftId, pending.value)
      if (token !== epoch) return
      if (!saving.value) { applyState(result); checking.value = false }
    } catch {
      if (token !== epoch) return
      notice.value = '暂时无法查询图片任务，正在重新连接…'
      // 首次未确认和已有任务都保持只读；网络异常不改写批次状态。
    } finally {
      if (token === epoch && active.value && busy.value) timer = setTimeout(() => void poll(token), 2000)
    }
  }
  watch([() => active.value, () => draft.value.draftId], ([opened, id]) => {
    ++epoch
    clearTimeout(timer)
    batch.value = null; pending.value = ''; past.value = []; future.value = []
    processingBefore = null; observedBatch = ''; saving.value = false; notice.value = ''
    acceptedSignature = signature(snapshotDraftImages(draft.value))
    if (!opened || !id) { checking.value = false; return }
    checking.value = true
    try { pending.value = localStorage.getItem(key(id)) || '' } catch { /* 本地存储不可用时按草稿查询。 */ }
    void poll()
  }, { immediate: true })
  watch(() => signature(snapshotDraftImages(draft.value)), value => {
    if (value !== acceptedSignature && !saving.value && !pending.value && !checking.value) {
      past.value = []; future.value = []
      acceptedSignature = value
    }
  })
  onScopeDispose(() => { ++epoch; clearTimeout(timer) })

  async function start(operation: 'translate' | 'edit', ids: string[], prompt = '') {
    if (busy.value || !ids.length) return
    const token = epoch
    const targets = [...ids]
    saving.value = true; notice.value = ''
    try {
      if (!await options.save()) { notice.value = '保存草稿失败，尚未开始处理。'; return }
      if (token !== epoch) return
      processingBefore = snapshotDraftImages(draft.value)
      processingLabel = `${operation === 'translate' ? '翻译图片' : 'AI 修改'} ${targets.length} 张`
      pending.value = `draft_images_${crypto.randomUUID()}`
      remember(draft.value.draftId, pending.value)
      future.value = []
      const result = await processDraftImages(draft.value, operation, targets, prompt, pending.value)
      if (token === epoch) applyState(result)
    } catch (error) {
      if (token !== epoch) return
      const status = (error as { response?: { status?: number }; status?: number }).response?.status ?? (error as { status?: number }).status
      if (status && status >= 400 && status < 500) {
        pending.value = ''; remember(draft.value.draftId, ''); processingBefore = null
        notice.value = error instanceof Error ? error.message : '提交未成功，请重新读取草稿。'
        checking.value = true
      } else notice.value = '提交结果尚未确认，正在查询原任务…'
    } finally {
      if (token === epoch) { saving.value = false; clearTimeout(timer); if (busy.value) timer = setTimeout(() => void poll(token), 1000) }
    }
  }

  async function planSet(ids: string[], count: number, prompt: string, skuIds: string[] = []) {
    if (busy.value || !ids.length) return
    const token = epoch
    saving.value = true; notice.value = ''
    try {
      if (!await options.save()) { notice.value = '保存草稿失败，尚未规划套图。'; return }
      if (token !== epoch) return
      const result = await planDraftImageSet(draft.value, ids, count, prompt, skuIds)
      if (token === epoch) return result
    } catch (error) {
      if (token === epoch) notice.value = error instanceof Error ? error.message : '套图规划失败，请检查识图模型配置。'
    } finally { if (token === epoch) saving.value = false }
  }

  async function generateSet(plan?: ImageSetPlan, updatedAt = draft.value.updatedAt, retryPageIds?: string[]) {
    if (busy.value) return
    const base = batch.value
    if (!plan && (!base?.image_set || !retryPageIds?.length)) return
    const token = epoch
    saving.value = true; notice.value = ''
    try {
      pending.value = `draft_image_set_${crypto.randomUUID()}`
      remember(draft.value.draftId, pending.value)
      const result = await generateDraftImageSet(draft.value, pending.value, {
        plan, updatedAt, baseRequestId: plan ? undefined : base!.conversation_id, retryPageIds,
      })
      if (token === epoch) applyState(result)
    } catch (error) {
      if (token !== epoch) return
      const status = (error as { response?: { status?: number }; status?: number }).response?.status ?? (error as { status?: number }).status
      if (status && status >= 400 && status < 500) {
        pending.value = ''; remember(draft.value.draftId, '')
        notice.value = error instanceof Error ? error.message : '套图提交失败，请重新读取草稿。'
        checking.value = true
      } else notice.value = '套图提交结果尚未确认，正在查询原任务…'
    } finally {
      if (token === epoch) { saving.value = false; clearTimeout(timer); if (busy.value) timer = setTimeout(() => void poll(token), 1000) }
    }
  }

  async function commit(label: string, change: (copy: DraftDetail) => void, direction?: 'undo' | 'redo') {
    if (busy.value) return
    const token = epoch
    const before = snapshotDraftImages(draft.value)
    const next = clone(draft.value)
    change(next)
    if (signature(before) === signature(snapshotDraftImages(next))) return
    saving.value = true; notice.value = ''
    try {
      const result = await saveDraft(next)
      if (token !== epoch) return
      accept(result)
      if (direction === 'undo') future.value.push(past.value.pop()!)
      else if (direction === 'redo') past.value.push(future.value.pop()!)
      else record(label, before)
      if (!batch.value?.image_set) batch.value = null
      notice.value = `${direction === 'undo' ? '已撤销：' : direction === 'redo' ? '已重做：' : '已保存：'}${label}`
    } catch (error) {
      if (token !== epoch) return
      notice.value = error instanceof Error ? error.message : '保存失败，请重试。'
      // 回执丢失时重新读取真实状态；不把本地历史游标当作已保存事实。
      try {
        const result = await fetchDraftImageState(draft.value.draftId)
        if (token === epoch) { accept(result); past.value = []; future.value = []; notice.value += ' 已重新读取草稿，请核对图片。' }
      } catch { if (token === epoch) { checking.value = true; notice.value = '保存结果待确认，正在重新读取…' } }
    } finally {
      if (token === epoch) { saving.value = false; clearTimeout(timer); if (busy.value) timer = setTimeout(() => void poll(token), 1000) }
    }
  }
  const normalize = (copy: DraftDetail) => { copy.images = copy.images.map((image, order) => ({ ...image, order, role: order === 0 ? 'main' : image.role === 'main' ? 'detail' : image.role })) }
  async function add(ids: string[]) {
    await commit(`添加 ${ids.length} 张共用图`, copy => {
      for (const id of ids) if (!copy.images.some(image => image.assetId === id)) copy.images.push({ assetId: id, role: 'detail', order: copy.images.length })
      normalize(copy)
    })
  }
  async function upload(files: File[]) {
    if (busy.value || !files.length) return
    const token = epoch
    saving.value = true
    try {
      const uploads = await Promise.all(files.map(async file => ({ filename: file.name, data_url: await readFile(file) })))
      const result = await uploadImages(options.product.value, uploads)
      if (token !== epoch) return
      options.accept(result)
      const uploadedIds = result.raw?.uploaded_image_ids
      if (!Array.isArray(uploadedIds) || !uploadedIds.every(id => typeof id === 'string')) throw new Error('图片已上传，但未确认添加范围，请从素材中选择。')
      const ids = uploadedIds as string[]
      saving.value = false
      await add(ids)
    } catch (error) { if (token === epoch) notice.value = error instanceof Error ? error.message : '上传失败。' }
    finally { if (token === epoch) saving.value = false }
  }
  return {
    busy, batch, notice, undoLabel, redoLabel, start, add, upload, planSet, generateSet,
    applySet: (refs: DraftImageRef[], skuPageKeys: string[] = []) => {
      const current = batch.value
      if (!current?.image_set || (!refs.length && !skuPageKeys.length)) return
      const successful = new Map(current.items.filter(item => item.status === 'success').map(item => [item.source_id, item.result_asset_ids || []]))
      const available = new Set(current.image_set.pages.filter(page => page.scope !== 'sku').flatMap(page => successful.get(page.key) || []))
      if (refs.some(ref => !available.has(ref.assetId))) return
      const skuChanges: Array<{ assetId: string; skuIds: string[] }> = []
      for (const key of new Set(skuPageKeys)) {
        const page = current.image_set.pages.find(page => page.key === key && page.scope === 'sku')
        const assetId = successful.get(key)?.[0]
        if (!page?.sku_ids?.length || !assetId) { notice.value = '所选 SKU 图片尚不可采用，请重新读取生成结果。'; return }
        if (page.sku_ids.some(id => !draft.value.skuItems.some(row => row.sku_id === id && row.selected))) {
          notice.value = '部分 SKU 已取消勾选或移出当前草稿，尚未采用图片，请重新选择规格并规划。'
          return
        }
        skuChanges.push({ assetId, skuIds: page.sku_ids })
      }
      return commit(`采用套图 ${refs.length + skuChanges.length} 张`, copy => {
        if (refs.length) { copy.images = clone(refs); normalize(copy) }
        for (const change of skuChanges) for (const id of change.skuIds) {
          const row = copy.skuItems.find(row => row.sku_id === id)!
          row.overrides.image_asset_id = change.assetId
        }
      })
    },
    remove: (ids: string[]) => commit(`移出 ${ids.length} 张共用图`, copy => { copy.images = copy.images.filter(image => !ids.includes(image.assetId)); normalize(copy) }),
    reorder: (ids: string[]) => commit('调整共用图顺序', copy => { const refs = new Map(copy.images.map(image => [image.assetId, image])); copy.images = ids.map(id => refs.get(id)!).filter(Boolean); normalize(copy) }),
    undo: () => { const operation = past.value.at(-1); if (operation) return commit(operation.label, copy => restoreDraftImages(copy, operation.before), 'undo') },
    redo: () => { const operation = future.value.at(-1); if (operation) return commit(operation.label, copy => restoreDraftImages(copy, operation.after), 'redo') },
  }
}
export type DraftImageWorkbenchController = ReturnType<typeof useDraftImageWorkbench>
