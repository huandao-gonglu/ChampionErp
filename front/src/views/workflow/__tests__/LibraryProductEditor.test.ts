import { flushPromises, shallowMount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, expect, it, vi } from 'vitest'
import * as catalog from '@/api/workflow/catalog'
import { normalizeProductIndexItem } from '@/api/workflow/normalizers/responses'
import { createEmptyProduct } from '@/constants/initialState'
import { useWorkflowStore } from '@/stores/workflow'
import DraftBoxPanel from '@/components/domain/DraftBoxPanel.vue'
import DraftSkuPanel from '@/components/domain/DraftSkuPanel.vue'
import DraftWorkspacePanel from '@/components/domain/DraftWorkspacePanel.vue'
import LibraryPanel from '@/components/domain/LibraryPanel.vue'
import ProductEditorPanel from '@/components/domain/ProductEditorPanel.vue'
import WorkflowView from '../WorkflowView.vue'

const rows = ['first', 'second'].map(id => normalizeProductIndexItem({ product_id: id, title: id }))
function response(productId: string) {
  return { ok: true, product: { ...createEmptyProduct(), productId, name: productId }, productsIndex: rows, imagePool: [] }
}
async function setup(tab: 'library' | 'drafts' = 'library') {
  vi.spyOn(window, 'matchMedia').mockImplementation(query => ({
    matches: false, media: query, onchange: null, addListener: vi.fn(), removeListener: vi.fn(),
    addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn(),
  }))
  const pinia = createPinia()
  setActivePinia(pinia)
  const store = useWorkflowStore()
  store.productsIndex = rows
  vi.spyOn(store, 'loadState').mockResolvedValue(undefined)
  vi.spyOn(store, 'hydrateTab').mockResolvedValue(undefined)
  const load = vi.spyOn(catalog, 'loadProduct')
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: WorkflowView }] })
  await router.push(`/?tab=${tab}`)
  const wrapper = shallowMount(WorkflowView, {
    attachTo: document.body,
    global: {
      plugins: [pinia, router],
      stubs: { LibraryPanel: false, DraftWorkspacePanel: false },
    },
  })
  await flushPromises()
  const library = tab === 'library' ? wrapper.getComponent(LibraryPanel) : null
  async function select(index: number) {
    await library!.findAll('button[title="编辑商品"]')[index].trigger('click')
    await flushPromises()
  }
  return { wrapper, store, library: library!, load, select }
}
afterEach(() => vi.restoreAllMocks())

it.each(['正常返回', '请求失败', '返回上一件商品'])('打开第一件，再点第二件：%s', async scenario => {
  const { wrapper, library, load, select } = await setup()
  try {
    expect(library.get('section').isVisible()).toBe(true)
    load.mockResolvedValueOnce(response('first'))
    await select(0)
    expect(wrapper.getComponent(ProductEditorPanel).props('product').productId).toBe('first')
    expect(wrapper.get('[data-testid="product-editor-back"]').text()).toContain('返回采集箱')
    expect(library.get('section').isVisible()).toBe(false)
    await wrapper.get('[data-testid="product-editor-back"]').trigger('click')
    expect(wrapper.findComponent(ProductEditorPanel).exists()).toBe(false)
    expect(library.get('section').isVisible()).toBe(true)
    if (scenario === '请求失败') load.mockRejectedValueOnce(new Error('连接超时'))
    else load.mockResolvedValueOnce(response(scenario === '正常返回' ? 'second' : 'first'))
    await select(1)
    expect(load).toHaveBeenLastCalledWith('second', rows[1].productFilePath)
    if (scenario === '正常返回') {
      expect(wrapper.getComponent(ProductEditorPanel).props('product').productId).toBe('second')
    } else {
      expect(wrapper.findComponent(ProductEditorPanel).exists()).toBe(false)
      expect(library.text()).toContain(scenario === '请求失败' ? '连接超时' : '商品不一致')
    }
  } finally { wrapper.unmount() }
})

it('先前的读取晚返回时仍显示最近选择的商品', async () => {
  const { wrapper, library, load } = await setup()
  try {
    let finishFirst!: (data: ReturnType<typeof response>) => void
    load.mockImplementationOnce(() => new Promise(resolve => { finishFirst = resolve }))
      .mockResolvedValueOnce(response('second'))
    library.vm.$emit('edit', rows[0])
    library.vm.$emit('edit', rows[1])
    await flushPromises()
    finishFirst(response('first'))
    await flushPromises()
    expect(wrapper.getComponent(ProductEditorPanel).props('product').productId).toBe('second')
  } finally { wrapper.unmount() }
})

it('草稿工作台原地替换视图，跳去补商品后可返回草稿再返回草稿箱', async () => {
  const { wrapper, store, load } = await setup('drafts')
  try {
    const draftItem = { draftId: 'draft-1', productId: 'first', platform: 'mercadolibre' as const }
    vi.spyOn(store, 'loadDraft').mockImplementation(async () => {
      store.currentDraft.draftId = 'draft-1'
      store.currentDraft.productId = 'first'
      store.currentDraft.platform = 'mercadolibre'
      return true
    })
    const draftBox = wrapper.getComponent(DraftBoxPanel)
    expect(draftBox.isVisible()).toBe(true)

    draftBox.vm.$emit('edit', draftItem, 'skus')
    await flushPromises()

    const draftWorkspace = wrapper.getComponent(DraftWorkspacePanel)
    expect(draftWorkspace.isVisible()).toBe(true)
    expect(draftBox.isVisible()).toBe(false)

    load.mockResolvedValueOnce(response('first'))
    wrapper.getComponent(DraftSkuPanel).vm.$emit('edit-product', 'sku-1')
    await flushPromises()

    expect(wrapper.getComponent(ProductEditorPanel).props('product').productId).toBe('first')
    expect(wrapper.get('[data-testid="product-editor-back"]').text()).toContain('返回草稿编辑')
    expect(draftWorkspace.isVisible()).toBe(false)
    expect(draftBox.isVisible()).toBe(false)

    await wrapper.get('[data-testid="product-editor-back"]').trigger('click')
    expect(wrapper.findComponent(ProductEditorPanel).exists()).toBe(false)
    expect(draftWorkspace.isVisible()).toBe(true)
    expect(draftBox.isVisible()).toBe(false)

    await draftWorkspace.findAll('button').find(b => b.text().includes('返回草稿箱'))!.trigger('click')
    expect(wrapper.findComponent(DraftWorkspacePanel).exists()).toBe(false)
    expect(draftBox.isVisible()).toBe(true)
  } finally { wrapper.unmount() }
})

it('商品编辑页各页签（含图片素材）与草稿编辑页共用同一右上角吸顶保存坐标', async () => {
  const { wrapper, load, select } = await setup('library')
  try {
    load.mockResolvedValueOnce(response('first'))
    await select(0)
    const header = wrapper.get('section[aria-label="商品编辑工作台"] header.page-header-sticky')
    expect(header.get('[data-testid="page-save-button"]').text()).toBe('保存商品')

    await header.findAll('button[role="tab"]').find(tab => tab.text().includes('图片素材'))!.trigger('click')
    expect(header.get('[data-testid="page-save-button"]').text()).toBe('保存商品素材')
  } finally { wrapper.unmount() }
})
