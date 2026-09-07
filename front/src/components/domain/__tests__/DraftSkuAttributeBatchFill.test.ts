// @vitest-environment jsdom
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import DraftSkuAttributeBatchFill from '../DraftSkuAttributeBatchFill.vue'
import DraftSkuPanel from '../DraftSkuPanel.vue'
import { createEmptyDraftDetail } from '@/constants/initialState'
import { useWorkflowStore } from '@/stores/workflow'
import { useWorkflowCatalogStore } from '@/stores/workflow/catalog'
import type { ProductSku } from '@/types/workflow'

const sourceCompleted = { filledCount: 0, needReview: [], warning: '' }
const completed = { filledCount: 1, needReview: [], warning: '' }
function deferred<T>() {
  let resolve!: (result: T) => void
  const promise = new Promise<T>(done => { resolve = done })
  return { promise, resolve }
}
function setup() {
  const catalog = useWorkflowCatalogStore()
  catalog.currentDraft = createEmptyDraftDetail('ozon')
  catalog.currentDraft.draftId = 'batch-draft'
  catalog.currentDraft.targetSites = [
    { platform: 'ozon', site: 'global', language: 'ru-RU', listingCurrency: 'RUB', categoryId: 'mask', categoryPath: '服饰 / 面罩', attributes: {} },
    { platform: 'yandex', site: 'global', language: 'ru-RU', listingCurrency: 'RUB', categoryId: 'mask-yandex', attributes: {} },
  ]
  catalog.currentDraft.skuItems = ['black', 'pink', 'unselected', 'inactive'].map(id => ({
    sku_id: id, sku: id, selected: id !== 'unselected', stock: '3', overrides: {}, pricing: {}, publications: {}, attributes_by_target: {},
  }))
  catalog.currentDraftProductContext.skuItems = catalog.currentDraft.skuItems.map(row => ({
    id: row.sku_id, name: row.sku_id === 'black' ? '暗夜黑' : '樱花粉', options: {}, active: row.sku_id !== 'inactive',
    source_sku_id: '', cost_cny: '', supplier_stock: '', image_asset_id: '', barcode: '', package_dimensions: {}, source_snapshot: {},
  } satisfies ProductSku))
  const workflow = useWorkflowStore()
  const editor = workflow.targetEditors[0]!
  editor.state.category = { platform: 'ozon', categoryId: 'mask', categoryPath: '服饰 / 面罩', fetchedAt: 'now', requiredAttributes: [
    { id: 'color', name: 'Цвет', required: true, variationRole: 'variant', valueMode: 'free_text' },
  ], optionalAttributes: [] }
  editor.state.categoryAttributeTranslations = { color: { label: '商品颜色', help: '', values: {} } }
  const wrapper = mount(DraftSkuAttributeBatchFill, { props: { targetKey: 'ozon:global', loading: false } })
  const start = async () => { await wrapper.findAll('button').find(button => button.text().startsWith('一键 AI'))!.trigger('click'); await flushPromises() }
  return { catalog, workflow, editor, wrapper, start }
}

describe('SKU 列表批量 AI 填写', () => {
  beforeEach(() => { setActivePinia(createPinia()); vi.clearAllMocks() })

  it('不展开详情即可看到批量入口，市场切换同时传给批量和单项编辑', async () => {
    const { catalog, wrapper: batch } = setup()
    batch.unmount()
    const wrapper = mount(DraftSkuPanel, { props: { draft: catalog.currentDraft, skus: catalog.currentDraftProductContext.skuItems, loading: false }, slots: {
      'batch-attributes': '<div data-testid="batch-slot">一键 AI 填写 SKU 属性：{{ params.targetKey }}</div>',
      attributes: '<div data-testid="row-slot">{{ params.targetKey }}</div>',
    } })
    expect(wrapper.get('[data-testid="batch-slot"]').text()).toContain('ozon:global')
    expect(wrapper.find('[data-testid="row-slot"]').exists()).toBe(false)
    await wrapper.findAll('label').find(label => label.text().startsWith('目标市场'))!.get('select').setValue('yandex:global')
    expect(wrapper.get('[data-testid="batch-slot"]').text()).toContain('yandex:global')
    await wrapper.findAll('button').find(button => button.text() === '属性 / 详情')!.trigger('click')
    expect(wrapper.get('[data-testid="row-slot"]').text()).toBe('yandex:global')
    wrapper.unmount()
  })

  it('只处理该市场已选且启用的 SKU，逐项等待保存并按规格列出复核项', async () => {
    const { editor, workflow, wrapper, start } = setup()
    const first = deferred<typeof completed>()
    const fill = vi.spyOn(editor.actions, 'fillAttributesByAi').mockResolvedValueOnce(sourceCompleted).mockReturnValueOnce(first.promise)
      .mockResolvedValueOnce({ filledCount: 0, needReview: ['color'], warning: '来源无法确认颜色' })
    const other = vi.spyOn(workflow.targetEditors[1]!.actions, 'fillAttributesByAi')
    await start()
    expect(fill.mock.calls).toEqual([['', true], ['black']])
    expect(wrapper.text()).toContain('正在填写 1 / 2：暗夜黑')
    expect(wrapper.findAll('button')[0]!.attributes('disabled')).toBeDefined()
    first.resolve(completed)
    await flushPromises()
    expect(fill.mock.calls).toEqual([['', true], ['black'], ['pink']])
    expect(other).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('已保存 2 / 2 个 SKU，新增 1 项属性，1 项待复核')
    expect(wrapper.get('li').text()).toContain('樱花粉待复核：商品颜色')
    expect(wrapper.get('li').text()).toContain('来源无法确认颜色')
    wrapper.unmount()
  })

  it('没有展开过属性时自动加载类目，再执行批量填写', async () => {
    const { editor, wrapper, start } = setup()
    const category = editor.state.category!
    editor.state.category = null
    const load = vi.spyOn(editor.actions, 'loadCategoryAttributes').mockImplementation(async () => { editor.state.category = category })
    const fill = vi.spyOn(editor.actions, 'fillAttributesByAi').mockResolvedValueOnce(sourceCompleted).mockResolvedValue(completed)
    await start()
    await flushPromises()
    expect(load).toHaveBeenCalledOnce()
    expect(fill.mock.calls).toEqual([['', true], ['black'], ['pink']])
    wrapper.unmount()
  })

  it('系统管理的分组字段不启动 SKU AI 填写', async () => {
    const { editor, wrapper, start } = setup()
    editor.state.category!.requiredAttributes = [{ id: '8292', name: '分组', required: true, variationRole: 'variant', managedBy: 'listing_grouping' }]
    const fill = vi.spyOn(editor.actions, 'fillAttributesByAi')
    await start()
    await flushPromises()
    expect(fill).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('当前类目没有可填写的 SKU 差异属性')
    wrapper.unmount()
  })

  it('类目读取失败时不调用 AI，保留可重试的错误说明', async () => {
    const { editor, wrapper, start } = setup()
    editor.state.category = null
    vi.spyOn(editor.actions, 'loadCategoryAttributes').mockImplementation(async () => {
      editor.state.categoryAttributeError = '当前市场的类目属性读取失败'
    })
    const fill = vi.spyOn(editor.actions, 'fillAttributesByAi')
    await start()
    await flushPromises()
    expect(fill).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('当前市场的类目属性读取失败')
    expect(wrapper.text()).not.toContain('批量填写完成')
    wrapper.unmount()
  })

  it('停止时等当前 SKU 保存，保留进度且不再请求下一项', async () => {
    const { editor, wrapper, start } = setup()
    const first = deferred<typeof completed>()
    const fill = vi.spyOn(editor.actions, 'fillAttributesByAi').mockResolvedValueOnce(sourceCompleted).mockReturnValue(first.promise)
    await start()
    await wrapper.findAll('button').find(button => button.text() === '完成当前 SKU 后停止')!.trigger('click')
    first.resolve(completed)
    await flushPromises()
    expect(fill.mock.calls).toEqual([['', true], ['black']])
    expect(wrapper.text()).toContain('批量填写已停止：已保存 1 / 2 个 SKU')
    expect(wrapper.text()).toContain('再次点击可继续补齐空缺')
    wrapper.unmount()
  })

  it('请求失败不宣称全部成功，显示对应 SKU 原因并保留已完成数量', async () => {
    const { editor, wrapper, start } = setup()
    vi.spyOn(editor.actions, 'fillAttributesByAi').mockResolvedValueOnce(sourceCompleted).mockResolvedValueOnce(completed).mockImplementationOnce(async () => {
      editor.state.error = '平台枚举查询失败'
      return undefined
    })
    await start()
    await flushPromises()
    expect(wrapper.text()).toContain('批量填写已停止：已保存 1 / 2 个 SKU')
    expect(wrapper.get('li').text()).toContain('樱花粉平台枚举查询失败')
    wrapper.unmount()
  })

  it('切换市场后停止旧批次，旧结果不显示在新市场', async () => {
    const { editor, wrapper, start } = setup()
    const first = deferred<typeof completed>()
    const fill = vi.spyOn(editor.actions, 'fillAttributesByAi').mockResolvedValueOnce(sourceCompleted).mockReturnValue(first.promise)
    await start()
    await wrapper.setProps({ targetKey: 'yandex:global' })
    first.resolve(completed)
    await flushPromises()
    expect(fill.mock.calls).toEqual([['', true], ['black']])
    expect(wrapper.text()).not.toContain('已保存')
    wrapper.unmount()
  })

  it('切换草稿不会继续调用旧批次或把结果显示在新草稿', async () => {
    const { catalog, editor, wrapper, start } = setup()
    const first = deferred<typeof completed>()
    const fill = vi.spyOn(editor.actions, 'fillAttributesByAi').mockResolvedValueOnce(sourceCompleted).mockReturnValue(first.promise)
    await start()
    catalog.currentDraft.draftId = 'another-draft'
    await wrapper.vm.$nextTick()
    first.resolve(completed)
    await flushPromises()
    expect(fill.mock.calls).toEqual([['', true], ['black']])
    expect(wrapper.text()).not.toContain('已保存')
    wrapper.unmount()
  })

  it('关闭编辑区后仅等待当前请求结束，不再启动下一项', async () => {
    const { editor, wrapper, start } = setup()
    const first = deferred<typeof completed>()
    const fill = vi.spyOn(editor.actions, 'fillAttributesByAi').mockResolvedValueOnce(sourceCompleted).mockReturnValue(first.promise)
    await start()
    wrapper.unmount()
    first.resolve(completed)
    await flushPromises()
    expect(fill.mock.calls).toEqual([['', true], ['black']])
  })

  it('保存返回同一草稿的新对象时继续下一项，不误判为切换草稿', async () => {
    const { catalog, editor, wrapper, start } = setup()
    const fill = vi.spyOn(editor.actions, 'fillAttributesByAi').mockImplementation(async () => {
      catalog.currentDraft = { ...catalog.currentDraft, skuItems: [...catalog.currentDraft.skuItems] }
      return completed
    })
    await start()
    await flushPromises()
    expect(fill.mock.calls).toEqual([['', true], ['black'], ['pink']])
    expect(wrapper.text()).toContain('批量填写完成')
    wrapper.unmount()
  })

  it('没有已选 SKU 或类目时禁用入口并给出类目提示' , async () => {
    const { catalog, editor, wrapper } = setup()
    catalog.currentDraft.skuItems.forEach(row => { row.selected = false })
    await wrapper.vm.$nextTick()
    expect(wrapper.findAll('button')[0]!.attributes('disabled')).toBeDefined()
    editor.draft.categoryId = ''
    await wrapper.vm.$nextTick()
    expect(wrapper.text()).toContain('请先在“类目/属性”为当前市场选择类目')
    wrapper.unmount()
  })
})
