import { flushPromises, shallowMount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, expect, it, vi } from 'vitest'
import * as catalog from '@/api/workflow/catalog'
import { normalizeProductIndexItem } from '@/api/workflow/normalizers/responses'
import { createEmptyProduct } from '@/constants/initialState'
import { useWorkflowStore } from '@/stores/workflow'
import LibraryPanel from '@/components/domain/LibraryPanel.vue'
import ProductEditorPanel from '@/components/domain/ProductEditorPanel.vue'
import WorkflowView from '../WorkflowView.vue'

const rows = ['first', 'second'].map(id => normalizeProductIndexItem({ product_id: id, title: id }))
function response(productId: string) {
  return { ok: true, product: { ...createEmptyProduct(), productId, name: productId }, productsIndex: rows, imagePool: [] }
}
async function setup() {
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
  await router.push('/?tab=library')
  const wrapper = shallowMount(WorkflowView, { global: { plugins: [pinia, router], stubs: { LibraryPanel: false } } })
  await flushPromises()
  const library = wrapper.getComponent(LibraryPanel)
  async function select(index: number) {
    await library.findAll('button[title="编辑商品"]')[index].trigger('click')
    await flushPromises()
  }
  return { wrapper, library, load, select }
}
afterEach(() => vi.restoreAllMocks())

it.each(['正常返回', '请求失败', '返回上一件商品'])('打开第一件，再点第二件：%s', async scenario => {
  const { wrapper, library, load, select } = await setup()
  try {
    load.mockResolvedValueOnce(response('first'))
    await select(0)
    expect(wrapper.getComponent(ProductEditorPanel).props('product').productId).toBe('first')
    await wrapper.findAll('button').find(button => button.text() === '关闭')!.trigger('click')
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
