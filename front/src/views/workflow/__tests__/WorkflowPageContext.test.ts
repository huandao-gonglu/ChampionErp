import { shallowMount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import * as catalogApi from '@/api/workflow/catalog'
import * as draftImagesApi from '@/api/draftImages'
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
