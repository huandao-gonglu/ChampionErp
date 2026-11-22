import { shallowMount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import * as catalogApi from '@/api/workflow/catalog'
import * as stateApi from '@/api/workflow/state'
import * as draftImagesApi from '@/api/draftImages'
import * as desktopApi from '@/api/desktopHandoff'
import { dismissDesktopLaunch, receivedDesktopHandoff, type DesktopHandoffState } from '@/utils/desktopHandoff'
import { setOperationAvailability } from '@/utils/desktop'
import { normalizeDraftMutation } from '@/api/workflow/normalizers'
import WorkflowView from '../WorkflowView.vue'
import DraftBoxPanel from '@/components/domain/DraftBoxPanel.vue'
import TaskQueuePanel from '@/components/domain/TaskQueuePanel.vue'
import { createEmptyDraftDetail, createEmptyProduct } from '@/constants/initialState'
import { useWorkflowStore } from '@/stores/workflow'
import { useWorkflowCatalogStore } from '@/stores/workflow/catalog'
import { useAiPageContextStore } from '@/stores/aiPageContext'
import type { DraftIndexItem } from '@/types/workflow'

beforeEach(() => {
  vi.spyOn(window, 'matchMedia').mockImplementation(query => ({
    matches: false, media: query, onchange: null,
    addListener: vi.fn(), removeListener: vi.fn(),
    addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn(),
  }))
})
afterEach(() => vi.restoreAllMocks())

it('关闭任务详情时移除任务深链，保留当前任务队列及业务来源', async () => {
  const pinia = createPinia()
  setActivePinia(pinia)
  const workflow = useWorkflowStore()
  vi.spyOn(workflow, 'loadState').mockResolvedValue(undefined)
  vi.spyOn(workflow, 'hydrateTab').mockResolvedValue(undefined)
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/', name: 'WorkflowHome', component: WorkflowView },
  ] })
  await router.push('/?tab=publish&source=online&task_id=online:sync-1')
  const wrapper = shallowMount(WorkflowView, {
    global: { plugins: [pinia, router] },
  })
  try {
    await flushPromises()
    expect(wrapper.getComponent(TaskQueuePanel).props('initialTaskId')).toBe('online:sync-1')
    wrapper.getComponent(TaskQueuePanel).vm.$emit('close-task')
    await flushPromises()
    expect(router.currentRoute.value.query).toEqual({ tab: 'publish', source: 'online' })
    expect(wrapper.getComponent(TaskQueuePanel).props('initialTaskId')).toBe('')
  } finally { wrapper.unmount() }
})

it('直接进入草稿箱时并行读取配置和列表，列表刷新期间仍可编辑已显示的草稿', async () => {
  const pinia = createPinia()
  setActivePinia(pinia)
  const workflow = useWorkflowStore()
  const row = normalizeDraftMutation({ draftsIndex: [{ draft_id: 'd', platform: 'yandex' }] }).draftsIndex[0]
  workflow.draftsIndex = [row]
  let finishSettings!: () => void
  let finishList!: (rows: DraftIndexItem[]) => void
  const settings = vi.spyOn(stateApi, 'fetchState').mockImplementationOnce(() => new Promise(resolve => {
    finishSettings = () => resolve({ schemaVersion: 1, appConfig: {}, storeConfig: {}, storeAuthSummary: {}, outputDir: '', platformOptions: [] })
  })).mockResolvedValue({ schemaVersion: 1, appConfig: {}, storeConfig: {}, storeAuthSummary: {}, outputDir: '',
    platformOptions: [], product: createEmptyProduct(), imagePool: [] })
  const index = vi.spyOn(catalogApi, 'fetchDraftsIndex').mockImplementationOnce(() => new Promise(resolve => { finishList = resolve }))
  vi.spyOn(catalogApi, 'fetchProductsIndex').mockResolvedValue([])
  const detail = vi.spyOn(catalogApi, 'loadDraft').mockResolvedValue(normalizeDraftMutation({ draft: { draft_id: 'd', platform: 'yandex' } }))
  vi.spyOn(draftImagesApi, 'fetchDraftImageState').mockResolvedValue({ unchanged: true, batch: null })
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', name: 'WorkflowHome', component: WorkflowView }] })
  await router.push('/?tab=drafts')
  const wrapper = shallowMount(WorkflowView, { global: { plugins: [pinia, router], stubs: { DraftBoxPanel: false } } })
  try {
    expect(settings.mock.calls).toEqual([[true]])
    expect(index).toHaveBeenCalledOnce()
    expect(detail).not.toHaveBeenCalled()
    const edit = wrapper.findAll('button').find(button => button.text() === '编辑')!
    expect(edit.element.disabled).toBe(false)
    await edit.trigger('click')
    await flushPromises()
    expect(detail.mock.calls).toEqual([['d']])
    expect(workflow.draftsIndex).toEqual([row])
    finishSettings()
    finishList([row])
    await flushPromises()
    wrapper.getComponent({ name: 'DraftWorkspacePanel' }).vm.$emit('close')
    await router.push('/?tab=library')
    await flushPromises()
    expect(settings.mock.calls).toEqual([[true]])
    expect(catalogApi.fetchProductsIndex).toHaveBeenCalledWith('list')
    await router.push('/')
    await flushPromises()
    expect(settings.mock.calls).toEqual([[true], [false]])
  } finally { wrapper.unmount() }
})

it('真实工作台先传勾选范围，打开和切换编辑草稿后优先传编辑对象，关闭及离开时清理', async () => {
  localStorage.removeItem('ai-chat-read-background')
  const pinia = createPinia()
  setActivePinia(pinia)
  const workflow = useWorkflowStore()
  const catalog = useWorkflowCatalogStore()
  const context = useAiPageContextStore()
  vi.spyOn(workflow, 'loadState').mockResolvedValue(undefined)
  vi.spyOn(workflow, 'hydrateTab').mockResolvedValue(undefined)
  vi.spyOn(workflow, 'loadDraft').mockImplementation(async (item) => {
    catalog.currentDraft = { ...createEmptyDraftDetail(), draftId: item.draftId, productId: item.productId, title: '本地化文案' }
    catalog.currentDraftProductContext.sourceTitle = '采集商品 ' + item.productId
    catalog.currentDraftProductContext.title = '内部名称'
    return true
  })
  const rows: DraftIndexItem[] = ['a', 'b'].map(id => ({
    draftId: `draft-${id}`, productId: `product-${id}`, title: id,
    platform: 'yandex', status: 'claimed', targetSites: [], language: 'ru-RU',
    sourceProductId: '', platforms: ['yandex'], site: 'global', productTitle: id,
    mainImage: '', sourcePlatform: '1688', sourceUrl: '', categoryId: '', categoryPath: '',
    publishStatus: '', createdAt: '', updatedAt: '', productFilePath: '', raw: {},
  }))
  catalog.draftsIndex = rows
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/', name: 'WorkflowHome', component: WorkflowView },
  ] })
  await router.push('/?tab=drafts')
  const wrapper = shallowMount(WorkflowView, {
    global: { plugins: [pinia, router], stubs: { DraftBoxPanel: false } },
  })
  try {
    await flushPromises()
    await wrapper.get('[aria-label="全选当前草稿"]').setValue(true)
    expect(context.snapshot()).toEqual({ page: 'drafts', selected_draft_ids: ['draft-a', 'draft-b'] })
    const box = wrapper.getComponent(DraftBoxPanel)
    box.vm.$emit('edit', rows[0])
    await flushPromises()
    expect(context.snapshot()).toEqual({ page: 'draft_editor', section: 'text', draft_id: 'draft-a', product_id: 'product-a' })
    box.vm.$emit('edit', rows[1])
    await flushPromises()
    expect(context.snapshot()?.draft_id).toBe('draft-b')
    expect(wrapper.getComponent({ name: 'DraftWorkspacePanel' }).props('draftTitle')).toBe('采集商品 product-b')
    wrapper.getComponent({ name: 'DraftWorkspacePanel' }).vm.$emit('close')
    await flushPromises()
    expect(context.snapshot()).toEqual({ page: 'drafts', selected_draft_ids: ['draft-a', 'draft-b'] })
    await router.push('/?tab=library')
    await flushPromises()
    expect(context.snapshot()).toEqual({ page: 'library' })
  } finally {
    wrapper.unmount()
  }
  expect(context.snapshot()).toBeUndefined()
})

it('重新加载草稿后，图片编辑和 SKU 详情都使用该次返回的素材', async () => {
  const pinia = createPinia()
  setActivePinia(pinia)
  const workflow = useWorkflowStore()
  vi.spyOn(workflow, 'loadState').mockResolvedValue(undefined)
  vi.spyOn(workflow, 'hydrateTab').mockResolvedValue(undefined)
  const loadProduct = vi.spyOn(workflow, 'loadProduct').mockResolvedValue(true)
  const item = { draft_id: 'draft-1', product_id: 'product-1', platform: 'yandex' }
  function response(imageId: string) {
    const sku = { id: 'sku-1', name: '15 升', active: true, image_asset_id: 'old',
      options: {}, package_dimensions: {}, supplier_stock: '1' }
    return normalizeDraftMutation({
      draft: { ...item, images: [{ asset_id: imageId, role: 'main', order: 0 }], sku_items: [{
        sku_id: sku.id, selected: true, stock: '1', overrides: { image_asset_id: imageId }, pricing: {}, publications: {},
      }] },
      productContext: { product_id: item.product_id, sku_items: [sku], image_pool: [{ id: imageId, preview_url: '/' + imageId + '.jpg' }] },
      draftsIndex: [item],
    })
  }
  const initial = response('old')
  vi.spyOn(catalogApi, 'loadDraft').mockResolvedValue(initial)
  const product = createEmptyProduct()
  product.productId = item.product_id
  product.source.imagePool = initial.productContext.imagePool
  vi.spyOn(draftImagesApi, 'fetchDraftImageState').mockResolvedValue({
    ...initial, product, imagePool: product.source.imagePool, batch: null,
  })
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/', name: 'WorkflowHome', component: WorkflowView },
  ] })
  await router.push('/?tab=drafts')
  const wrapper = shallowMount(WorkflowView, { global: { plugins: [pinia, router], stubs: {
    DraftWorkspacePanel: false, DraftImageWorkbench: false, DraftSkuPanel: false,
    SkuCollapsibleSection: false, SkuImagePicker: false, Teleport: true,
  } } })
  const button = (label: string) => wrapper.findAll('button').find(node => node.text() === label)!
  try {
    await flushPromises()
    wrapper.getComponent(DraftBoxPanel).vm.$emit('edit', initial.draftsIndex[0], 'images')
    await flushPromises()
    expect(wrapper.get('[data-region="sku"] img').attributes('src')).toBe('/old.jpg')

    vi.mocked(catalogApi.loadDraft).mockResolvedValue(response('generated'))
    await button('重新加载最新数据').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-region="sku"] img').attributes('src')).toBe('/generated.jpg')
    expect(wrapper.get('[data-region="common"] img').attributes('src')).toBe('/generated.jpg')

    await button('SKU').trigger('click')
    await button('属性 / 详情').trigger('click')
    expect(wrapper.get('img[alt="15 升 的销售图片"]').attributes('src')).toBe('/generated.jpg')
    expect(loadProduct).not.toHaveBeenCalled()
  } finally { wrapper.unmount() }
})

it.each(['edit', 'translate'] as const)('仅恢复草稿图片 %s 确认框时等待用户提交', async action => {
  const pinia = createPinia()
  setActivePinia(pinia)
  const workflow = useWorkflowStore()
  vi.spyOn(workflow, 'loadState').mockResolvedValue(undefined)
  vi.spyOn(workflow, 'hydrateTab').mockResolvedValue(undefined)
  const saved = normalizeDraftMutation({
    draft: { draft_id: 'draft-edit', product_id: 'p', platform: 'yandex', images: [{ asset_id: 'cover', order: 0, role: 'main' }] },
    productContext: { product_id: 'p', image_pool: [{ id: 'cover', preview_url: '/cover.jpg' }] },
  })
  vi.spyOn(catalogApi, 'loadDraft').mockResolvedValue(saved)
  vi.spyOn(draftImagesApi, 'fetchDraftImageState').mockResolvedValue({ ...saved, product: createEmptyProduct(), imagePool: saved.productContext.imagePool, batch: null })
  const process = vi.spyOn(draftImagesApi, 'processDraftImages')
  const confirm = vi.spyOn(desktopApi, 'confirmDesktopHandoff').mockResolvedValue(undefined)
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', name: 'WorkflowHome', component: WorkflowView }] })
  await router.push('/?tab=drafts')
  const wrapper = shallowMount(WorkflowView, { global: { plugins: [pinia, router], stubs: {
    DraftWorkspacePanel: false, DraftImageWorkbench: false, WorkspaceDialog: false, Teleport: true,
  } } })
  try {
    receivedDesktopHandoff.value = { requestId: 'draft-image-edit', target: '/?tab=drafts', state: { workflow: {
      draft: saved.draft, draftContext: saved.productContext, draftTab: 'images',
      draftImageAction: { action, imageIds: ['cover'], prompt: action === 'edit' ? '保留原色' : '' },
    } } }
    await flushPromises()
    expect(wrapper.find('dialog[open] form').exists()).toBe(true)
    if (action === 'edit') expect(wrapper.get('dialog[open] textarea').element).toHaveProperty('value', '保留原色')
    expect(wrapper.get('input[aria-label="选择处理图片 cover"]').element).toHaveProperty('checked', true)
    expect(confirm).toHaveBeenCalledWith('draft-image-edit')
    expect(process).not.toHaveBeenCalled()
  } finally {
    receivedDesktopHandoff.value = null
    wrapper.unmount()
  }
})

it('网页确认翻译后桌面等待任务状态再提交，重复唤起复用原批次', async () => {
  const pinia = createPinia()
  setActivePinia(pinia)
  const workflow = useWorkflowStore()
  const saved = normalizeDraftMutation({
    draft: { draft_id: 'translate', product_id: 'p', platform: 'yandex', updated_at: 'v1', language: 'ru',
      images: [{ asset_id: 'cover', order: 0, role: 'main' }, { asset_id: 'other', order: 1, role: 'detail' }] },
    productContext: { product_id: 'p', image_pool: [{ id: 'cover' }, { id: 'other' }] },
    draftsIndex: [{ draft_id: 'translate', product_id: 'p', platform: 'yandex' }],
  })
  vi.spyOn(workflow, 'loadState').mockResolvedValue(undefined)
  vi.spyOn(workflow, 'hydrateTab').mockResolvedValue(undefined)
  vi.spyOn(catalogApi, 'fetchDraftsIndex').mockResolvedValue(saved.draftsIndex)
  vi.spyOn(catalogApi, 'fetchProductsIndex').mockResolvedValue([])
  vi.spyOn(catalogApi, 'loadDraft').mockResolvedValue(saved)
  const save = vi.spyOn(workflow, 'saveCurrentDraft').mockImplementation(async () => {
    workflow.currentDraft.updatedAt = 'v2'
    return true
  })
  const query = vi.spyOn(draftImagesApi, 'fetchDraftImageState').mockResolvedValue({ unchanged: true, batch: null })
  const submit = vi.spyOn(draftImagesApi, 'processDraftImages').mockImplementation(async (_draft, _op, _ids, _prompt, requestId) => ({
    ...saved, product: createEmptyProduct(), imagePool: saved.productContext.imagePool,
    batch: { conversation_id: requestId, product_id: 'p', draft_id: 'translate', prompt: '', created_at: '', updated_at: '',
      total: 1, queued: 1, running: 0, completed: 0, success: 0, failed: 0, terminal: false, items: [] },
  }))
  const create = vi.spyOn(desktopApi, 'createDesktopHandoff').mockRejectedValue(new Error('测试中通过交接状态模拟桌面接收'))
  const confirm = vi.spyOn(desktopApi, 'confirmDesktopHandoff').mockResolvedValue(undefined)
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', name: 'WorkflowHome', component: WorkflowView }] })
  await router.push('/?tab=drafts')
  const mountView = () => shallowMount(WorkflowView, { attachTo: document.body, global: { plugins: [pinia, router], stubs: {
    DraftWorkspacePanel: false, DraftImageWorkbench: false, WorkspaceDialog: false, Teleport: true,
  } } })
  let wrapper = mountView()
  try {
    setOperationAvailability({ desktop_use_cases: ['image.translate'], desktop_reason: '', blocked_use_cases: {} })
    await flushPromises()
    wrapper.getComponent(DraftBoxPanel).vm.$emit('edit', saved.draftsIndex[0], 'images')
    await flushPromises()
    await wrapper.get('input[aria-label="选择处理图片 cover"]').setValue(true)
    await wrapper.findAll('button').find(b => b.text() === '翻译图片')!.trigger('click')
    expect(create).not.toHaveBeenCalled()
    await wrapper.get('dialog[open] form').trigger('submit')
    await flushPromises()
    expect(save).toHaveBeenCalledOnce()
    expect(submit).not.toHaveBeenCalled()
    const [target, state] = create.mock.calls[0]
    const handoff = { requestId: 'open-translation', target, state: state as DesktopHandoffState }
    const requestId = handoff.state.workflow!.draftImageAction!.translationRequestId
    expect(requestId).toBeTruthy()
    expect(handoff.state.workflow!.draft!.updatedAt).toBe('v2')
    wrapper.unmount()

    window.championDesktop = { localOperations: true, openLicenses: vi.fn() }
    let finishQuery!: (value: draftImagesApi.DraftImagePoll) => void
    query.mockImplementationOnce(() => new Promise(resolve => { finishQuery = resolve }))
    wrapper = mountView()
    receivedDesktopHandoff.value = handoff
    await flushPromises()
    receivedDesktopHandoff.value = { ...handoff }
    await flushPromises()
    expect(submit).not.toHaveBeenCalled()
    expect(confirm).not.toHaveBeenCalled()
    finishQuery({ unchanged: true, batch: null })
    await flushPromises()
    expect(submit).toHaveBeenCalledOnce()
    expect(submit).toHaveBeenCalledWith(expect.objectContaining({ draftId: 'translate', updatedAt: 'v2' }), 'translate', ['cover'], '', requestId, expect.stringMatching(/^conversation_global_chat_/))
    expect(confirm).toHaveBeenCalledWith('open-translation')
    expect(wrapper.text()).toContain('正在处理 1 张图片')
    expect(wrapper.find('dialog[open] form').exists()).toBe(false)
    receivedDesktopHandoff.value = { ...handoff }
    await flushPromises()
    expect(submit).toHaveBeenCalledOnce()
    expect(save).toHaveBeenCalledOnce()
  } finally {
    receivedDesktopHandoff.value = null
    delete window.championDesktop
    dismissDesktopLaunch()
    wrapper.unmount()
  }
})

it('网页规划并编辑套图后交接桌面，桌面等待状态后打开套图抽屉并自动提交，重复唤起复用原批次', async () => {
  const pinia = createPinia()
  setActivePinia(pinia)
  const workflow = useWorkflowStore()
  const saved = normalizeDraftMutation({
    draft: { draft_id: 'set-draft', product_id: 'p', platform: 'yandex', updated_at: 'v1', language: 'ru',
      images: [{ asset_id: 'cover', order: 0, role: 'main' }, { asset_id: 'other', order: 1, role: 'detail' }] },
    productContext: { product_id: 'p', image_pool: [{ id: 'cover' }, { id: 'other' }] },
    draftsIndex: [{ draft_id: 'set-draft', product_id: 'p', platform: 'yandex' }],
  })
  const planned: draftImagesApi.ImageSetPlan = { style: '统一白底', pages: [
    { key: 'main', title: '主图', role: 'main', prompt: '原主图方案', source_image_ids: ['cover'] },
    { key: 'detail', title: '细节', role: 'detail', prompt: '原细节方案', source_image_ids: ['other'] },
  ] }
  vi.spyOn(workflow, 'loadState').mockResolvedValue(undefined)
  vi.spyOn(workflow, 'hydrateTab').mockResolvedValue(undefined)
  vi.spyOn(catalogApi, 'fetchDraftsIndex').mockResolvedValue(saved.draftsIndex)
  vi.spyOn(catalogApi, 'fetchProductsIndex').mockResolvedValue([])
  vi.spyOn(catalogApi, 'loadDraft').mockResolvedValue(saved)
  vi.spyOn(workflow, 'saveCurrentDraft').mockResolvedValue(true)
  vi.spyOn(draftImagesApi, 'planDraftImageSet').mockResolvedValue({ ok: true, plan: structuredClone(planned), updated_at: 'v1' })
  const oldFailedBatch = {
    conversation_id: 'old-translate-batch', product_id: 'p', draft_id: 'set-draft', prompt: '旧翻译', created_at: '', updated_at: '',
    total: 1, queued: 0, running: 0, completed: 1, success: 0, failed: 1, terminal: true,
    items: [{ index: 0, source_id: 'cover', source_url: '', status: 'failed' as const, error: '桌面连接已断开', outcome_unknown: false, result_urls: [], started_at: '', finished_at: '' }],
  }
  const query = vi.spyOn(draftImagesApi, 'fetchDraftImageState').mockResolvedValue({ unchanged: true, batch: oldFailedBatch })
  const submit = vi.spyOn(draftImagesApi, 'generateDraftImageSet').mockImplementation(async (_draft, requestId, options) => ({
    ...saved, product: createEmptyProduct(), imagePool: saved.productContext.imagePool,
    batch: { conversation_id: requestId, product_id: 'p', draft_id: 'set-draft', prompt: '', created_at: '', updated_at: '',
      total: 2, queued: 2, running: 0, completed: 0, success: 0, failed: 0, terminal: false, image_set: options.plan || planned,
      items: (options.plan || planned).pages.map((page, index) => ({ index, source_id: page.key, source_url: '', status: 'queued' as const, error: '', outcome_unknown: false, result_urls: [], started_at: '', finished_at: '' })) },
  }))
  const create = vi.spyOn(desktopApi, 'createDesktopHandoff').mockRejectedValue(new Error('测试中通过交接状态模拟桌面接收'))
  const confirm = vi.spyOn(desktopApi, 'confirmDesktopHandoff').mockResolvedValue(undefined)
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', name: 'WorkflowHome', component: WorkflowView }] })
  await router.push('/?tab=drafts')
  const mountView = () => shallowMount(WorkflowView, { attachTo: document.body, global: { plugins: [pinia, router], stubs: {
    DraftWorkspacePanel: false, DraftImageWorkbench: false, DraftImageSetDialog: false, WorkspaceDialog: false, Teleport: true,
  } } })
  let wrapper = mountView()
  try {
    setOperationAvailability({ desktop_use_cases: ['image.translate'], desktop_reason: '', blocked_use_cases: {} })
    await flushPromises()
    wrapper.getComponent(DraftBoxPanel).vm.$emit('edit', saved.draftsIndex[0], 'images')
    await flushPromises()
    await wrapper.findAll('button').find(b => b.text() === '生成套图')!.trigger('click')
    await flushPromises()
    await wrapper.findAll('button').find(b => b.text() === '自动规划套图')!.trigger('submit')
    await flushPromises()
    await wrapper.get('[data-testid="image-set-dialog"] textarea').setValue('极简浅灰棚拍')
    await wrapper.findAll('button').find(b => b.text() === '开始生成 2 张')!.trigger('click')
    await flushPromises()
    expect(submit).not.toHaveBeenCalled()
    expect(create).toHaveBeenCalledOnce()
    const [target, state] = create.mock.calls[0]
    const handoff = { requestId: 'open-image-set', target, state: state as DesktopHandoffState }
    const action = handoff.state.workflow!.draftImageSetAction!
    expect(action.requestId).toMatch(/^draft_image_set_/)
    expect(action.updatedAt).toBe('v1')
    expect(action.plan?.style).toBe('极简浅灰棚拍')
    wrapper.unmount()

    window.championDesktop = { localOperations: true, openLicenses: vi.fn() }
    let finishQuery!: (value: draftImagesApi.DraftImagePoll) => void
    query.mockImplementationOnce(() => new Promise(resolve => { finishQuery = resolve }))
    wrapper = mountView()
    receivedDesktopHandoff.value = handoff
    await flushPromises()
    expect(submit).not.toHaveBeenCalled()
    expect(confirm).not.toHaveBeenCalled()
    finishQuery({ unchanged: true, batch: oldFailedBatch })
    await flushPromises()
    expect(submit).toHaveBeenCalledOnce()
    expect(submit).toHaveBeenCalledWith(
      expect.objectContaining({ draftId: 'set-draft', updatedAt: 'v1' }),
      action.requestId,
      expect.objectContaining({ updatedAt: 'v1', plan: expect.objectContaining({ style: '极简浅灰棚拍' }) }),
      expect.stringMatching(/^conversation_global_chat_/),
    )
    expect(confirm).toHaveBeenCalledWith('open-image-set')
    expect(wrapper.get('[data-testid="image-set-dialog"]').text()).toContain('正在生成套图')
    expect(wrapper.get('[data-testid="image-set-dialog"]').text()).toContain('极简浅灰棚拍')

    // 重复收到同一交接不重复提交，仍停留在套图结果/进度视图。
    receivedDesktopHandoff.value = { ...handoff }
    await flushPromises()
    expect(submit).toHaveBeenCalledOnce()
    expect(wrapper.get('[data-testid="image-set-dialog"]').text()).toContain('正在生成套图')
  } finally {
    receivedDesktopHandoff.value = null
    delete window.championDesktop
    dismissDesktopLaunch()
    wrapper.unmount()
  }
})

it('套图交接期间草稿已更新时保留规划方案在抽屉中，核对后可按最新版本手动提交', async () => {
  const pinia = createPinia()
  setActivePinia(pinia)
  const workflow = useWorkflowStore()
  const webDraft = normalizeDraftMutation({
    draft: { draft_id: 'set-stale', product_id: 'p', platform: 'yandex', updated_at: 'v1', language: 'ru',
      images: [{ asset_id: 'cover', order: 0, role: 'main' }] },
    productContext: { product_id: 'p', image_pool: [{ id: 'cover' }] },
    draftsIndex: [{ draft_id: 'set-stale', product_id: 'p', platform: 'yandex' }],
  })
  const newerDraft = normalizeDraftMutation({
    draft: { draft_id: 'set-stale', product_id: 'p', platform: 'yandex', updated_at: 'v2', language: 'ru',
      images: [{ asset_id: 'cover', order: 0, role: 'main' }] },
    productContext: { product_id: 'p', image_pool: [{ id: 'cover' }] },
    draftsIndex: [{ draft_id: 'set-stale', product_id: 'p', platform: 'yandex' }],
  })
  const plan: draftImagesApi.ImageSetPlan = { style: '已编辑套图风格', pages: [
    { key: 'main', title: '主图', role: 'main', prompt: '保留颜色重新构图', source_image_ids: ['cover'] },
  ] }
  vi.spyOn(workflow, 'loadState').mockResolvedValue(undefined)
  vi.spyOn(workflow, 'hydrateTab').mockResolvedValue(undefined)
  vi.spyOn(catalogApi, 'fetchDraftsIndex').mockResolvedValue(newerDraft.draftsIndex)
  vi.spyOn(catalogApi, 'fetchProductsIndex').mockResolvedValue([])
  vi.spyOn(catalogApi, 'loadDraft').mockResolvedValue(newerDraft)
  vi.spyOn(draftImagesApi, 'fetchDraftImageState').mockResolvedValue({ unchanged: true, batch: null })
  const submit = vi.spyOn(draftImagesApi, 'generateDraftImageSet').mockResolvedValue({
    ...newerDraft, product: createEmptyProduct(), imagePool: newerDraft.productContext.imagePool,
    batch: { conversation_id: 'manual-set', product_id: 'p', draft_id: 'set-stale', prompt: '', created_at: '', updated_at: '',
      total: 1, queued: 1, running: 0, completed: 0, success: 0, failed: 0, terminal: false, image_set: plan,
      items: [{ index: 0, source_id: 'main', source_url: '', status: 'queued', error: '', outcome_unknown: false, result_urls: [], started_at: '', finished_at: '' }] },
  })
  const confirm = vi.spyOn(desktopApi, 'confirmDesktopHandoff').mockResolvedValue(undefined)
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', name: 'WorkflowHome', component: WorkflowView }] })
  await router.push('/?tab=drafts')
  window.championDesktop = { localOperations: true, openLicenses: vi.fn() }
  setOperationAvailability({ desktop_use_cases: ['image.translate'], desktop_reason: '', blocked_use_cases: {} })
  const wrapper = shallowMount(WorkflowView, { attachTo: document.body, global: { plugins: [pinia, router], stubs: {
    DraftWorkspacePanel: false, DraftImageWorkbench: false, DraftImageSetDialog: false, WorkspaceDialog: false, Teleport: true,
  } } })
  try {
    receivedDesktopHandoff.value = {
      requestId: 'stale-image-set', target: '/?tab=drafts',
      state: { workflow: {
        draft: webDraft.draft, draftContext: webDraft.productContext, draftTab: 'images',
        draftImageSetAction: { requestId: 'draft_image_set_stale', updatedAt: 'v1', plan },
      } },
    }
    await flushPromises()
    expect(submit).not.toHaveBeenCalled()
    expect(confirm).toHaveBeenCalledWith('stale-image-set')
    const dialog = wrapper.get('[data-testid="image-set-dialog"]')
    expect(dialog.text()).toContain('草稿已更新，请核对套图方案后重新生成')
    expect((dialog.get('textarea').element as HTMLTextAreaElement).value).toBe('已编辑套图风格')
    await wrapper.findAll('button').find(b => b.text() === '开始生成 1 张')!.trigger('click')
    await flushPromises()
    expect(submit).toHaveBeenCalledOnce()
    expect(submit).toHaveBeenCalledWith(
      expect.objectContaining({ draftId: 'set-stale', updatedAt: 'v2' }),
      expect.stringMatching(/^draft_image_set_/),
      expect.objectContaining({ updatedAt: 'v2', plan: expect.objectContaining({ style: '已编辑套图风格' }) }),
      expect.stringMatching(/^conversation_global_chat_/),
    )
  } finally {
    receivedDesktopHandoff.value = null
    delete window.championDesktop
    dismissDesktopLaunch()
    wrapper.unmount()
  }
})

it('网页端套图结果页点击单页重做，桌面端等待原批次后按固定 requestId 自动提交', async () => {
  const pinia = createPinia()
  setActivePinia(pinia)
  const workflow = useWorkflowStore()
  const saved = normalizeDraftMutation({
    draft: { draft_id: 'retry-draft', product_id: 'p', platform: 'yandex', updated_at: 'v1', language: 'ru',
      images: [{ asset_id: 'cover', order: 0, role: 'main' }] },
    productContext: { product_id: 'p', image_pool: [{ id: 'cover' }, { id: 'gen-1' }] },
    draftsIndex: [{ draft_id: 'retry-draft', product_id: 'p', platform: 'yandex' }],
  })
  const plan: draftImagesApi.ImageSetPlan = { style: '统一风格', pages: [
    { key: 'main', title: '主图', role: 'main', prompt: '主图设计', source_image_ids: ['cover'] },
  ] }
  const existingBatch = {
    conversation_id: 'set-base', product_id: 'p', draft_id: 'retry-draft', prompt: '', created_at: '', updated_at: '',
    total: 1, queued: 0, running: 0, completed: 1, success: 1, failed: 0, terminal: true, image_set: plan,
    items: [{ index: 0, source_id: 'main', source_url: '', status: 'success' as const, error: '', outcome_unknown: false, result_asset_ids: ['gen-1'], result_urls: ['/gen-1.png'], started_at: '', finished_at: '' }],
  }
  vi.spyOn(workflow, 'loadState').mockResolvedValue(undefined)
  vi.spyOn(workflow, 'hydrateTab').mockResolvedValue(undefined)
  vi.spyOn(catalogApi, 'fetchDraftsIndex').mockResolvedValue(saved.draftsIndex)
  vi.spyOn(catalogApi, 'fetchProductsIndex').mockResolvedValue([])
  vi.spyOn(catalogApi, 'loadDraft').mockResolvedValue(saved)
  vi.spyOn(draftImagesApi, 'fetchDraftImageState').mockResolvedValue({ unchanged: true, batch: existingBatch })
  const submit = vi.spyOn(draftImagesApi, 'generateDraftImageSet').mockImplementation(async (_draft, requestId) => ({
    ...saved, product: createEmptyProduct(), imagePool: saved.productContext.imagePool,
    batch: { ...existingBatch, conversation_id: requestId, terminal: false, completed: 0, success: 0, queued: 1,
      items: [{ ...existingBatch.items[0], status: 'queued' as const, result_asset_ids: [], result_urls: [] }] },
  }))
  const create = vi.spyOn(desktopApi, 'createDesktopHandoff').mockRejectedValue(new Error('测试中通过交接状态模拟桌面接收'))
  const confirm = vi.spyOn(desktopApi, 'confirmDesktopHandoff').mockResolvedValue(undefined)
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', name: 'WorkflowHome', component: WorkflowView }] })
  await router.push('/?tab=drafts')
  const mountView = () => shallowMount(WorkflowView, { attachTo: document.body, global: { plugins: [pinia, router], stubs: {
    DraftWorkspacePanel: false, DraftImageWorkbench: false, DraftImageSetDialog: false, WorkspaceDialog: false, Teleport: true,
  } } })
  let wrapper = mountView()
  try {
    setOperationAvailability({ desktop_use_cases: ['image.translate'], desktop_reason: '', blocked_use_cases: {} })
    await flushPromises()
    wrapper.getComponent(DraftBoxPanel).vm.$emit('edit', saved.draftsIndex[0], 'images')
    await flushPromises()
    await wrapper.findAll('button').find(b => b.text() === '查看套图')!.trigger('click')
    await flushPromises()
    await wrapper.get('button[aria-label="重做 主图"]').trigger('click')
    await flushPromises()
    expect(submit).not.toHaveBeenCalled()
    expect(create).toHaveBeenCalledOnce()
    const [target, state] = create.mock.calls[0]
    const handoff = { requestId: 'retry-image-set', target, state: state as DesktopHandoffState }
    const action = handoff.state.workflow!.draftImageSetAction!
    expect(action).toEqual({
      requestId: expect.stringMatching(/^draft_image_set_/),
      updatedAt: 'v1', baseRequestId: 'set-base', retryPageIds: ['main'],
    })
    wrapper.unmount()

    window.championDesktop = { localOperations: true, openLicenses: vi.fn() }
    wrapper = mountView()
    receivedDesktopHandoff.value = handoff
    await flushPromises()
    expect(submit).toHaveBeenCalledOnce()
    expect(submit).toHaveBeenCalledWith(
      expect.objectContaining({ draftId: 'retry-draft', updatedAt: 'v1' }),
      action.requestId,
      { plan: undefined, updatedAt: 'v1', baseRequestId: 'set-base', retryPageIds: ['main'] },
      expect.stringMatching(/^conversation_global_chat_/),
    )
    expect(confirm).toHaveBeenCalledWith('retry-image-set')
    expect(wrapper.get('[data-testid="image-set-dialog"]').text()).toContain('正在生成套图')
  } finally {
    receivedDesktopHandoff.value = null
    delete window.championDesktop
    dismissDesktopLaunch()
    wrapper.unmount()
  }
})
