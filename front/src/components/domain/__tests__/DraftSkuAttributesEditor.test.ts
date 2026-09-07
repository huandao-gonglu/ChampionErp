// @vitest-environment jsdom
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import DraftSkuAttributesEditor from '../DraftSkuAttributesEditor.vue'
import { createEmptyDraftDetail } from '@/constants/initialState'
import { useWorkflowStore } from '@/stores/workflow'
import { useWorkflowCatalogStore } from '@/stores/workflow/catalog'
import { fetchCategoryAttributeValues } from '@/api/workflow/publishing'
import type { ProductSku } from '@/types/workflow'

vi.mock('@/api/workflow/publishing', async importOriginal => ({
  ...await importOriginal<typeof import('@/api/workflow/publishing')>(),
  fetchCategoryAttributeValues: vi.fn(),
}))

const sku: ProductSku = { id: 'black', name: '暗夜黑', options: { 颜色: '暗夜黑' }, source_sku_id: 'source-black',
  cost_cny: '20', supplier_stock: '500', image_asset_id: '', barcode: '', package_dimensions: {}, active: true, source_snapshot: {} }

function setup() {
  const catalog = useWorkflowCatalogStore()
  catalog.currentDraft = createEmptyDraftDetail('ozon')
  catalog.currentDraft.draftId = 'sku-editor'
  catalog.currentDraft.targetSites = [{ platform: 'ozon', site: 'global', language: 'ru-RU', listingCurrency: 'RUB', categoryId: 'mask', categoryPath: '服饰 / 面罩', attributes: { size: '均码' } }]
  catalog.currentDraft.skuItems = ['black', 'pink'].map(id => ({ sku_id: id, sku: id, selected: true, stock: '3', overrides: {}, pricing: {}, publications: {}, attributes_by_target: {} }))
  const workflow = useWorkflowStore()
  const editor = workflow.targetEditors[0]!
  editor.state.category = { platform: 'ozon', categoryId: 'mask', categoryPath: '服饰 / 面罩', fetchedAt: 'now', requiredAttributes: [], optionalAttributes: [
    { id: 'color', name: 'Цвет', required: false, variationRole: 'variant', valueMode: 'strict_enum', isDictionary: true },
    { id: 'size', name: 'Размер', required: false, variationRole: 'variant', valueMode: 'free_text' },
    { id: 'brand', name: '品牌', required: false, valueMode: 'free_text' },
  ] }
  return { catalog, editor }
}

describe('SKU 使用真实类目属性编辑器', () => {
  beforeEach(() => { setActivePinia(createPinia()); vi.clearAllMocks() })

  it('直接显示平台差异字段，沿用共同值，选择枚举只写当前 SKU', async () => {
    const { catalog } = setup()
    vi.mocked(fetchCategoryAttributeValues).mockResolvedValue({ values: [{ id: '9007199254740993123', value: 'Черный', info: '', picture: '' }], hasMore: false, nextCursor: '', complete: true })
    const wrapper = mount(DraftSkuAttributesEditor, { props: { skuId: 'black', sku, targetKey: 'ozon:global' } })
    expect(wrapper.text()).toContain('颜色：暗夜黑')
    expect(wrapper.find('input[data-attribute-id="brand"]').exists()).toBe(false)
    expect(wrapper.get<HTMLInputElement>('input[data-attribute-id="size"]').element.value).toBe('均码')
    await wrapper.get('input[data-attribute-id="color"]').trigger('focus')
    await flushPromises()
    await wrapper.findAll('button').find(button => button.text() === 'Черный')!.trigger('click')
    expect(catalog.currentDraft.skuItems[0]?.attributes_by_target['ozon:global']?.color).toEqual({ values: [{ dictionaryValueId: '9007199254740993123', value: 'Черный' }] })
    expect(catalog.currentDraft.skuItems[1]?.attributes_by_target).toEqual({})
    expect(catalog.currentDraft.targetSites[0]?.attributes).toEqual({ size: '均码' })
    wrapper.unmount()
  })

  it('Mercado 自定义规格编辑仅写当前市场和当前 SKU', async () => {
    const { catalog } = setup()
    catalog.currentDraft.targetSites = [{ platform: 'mercadolibre', site: 'CBT', language: 'en', listingCurrency: 'USD', categoryId: 'CBT1', attributes: {} }]
    const editor = useWorkflowStore().targetEditors[0]!
    editor.state.category = { platform: 'mercadolibre', categoryId: 'CBT1', categoryPath: 'Accessories', fetchedAt: 'now', requiredAttributes: [], optionalAttributes: [] }
    const wrapper = mount(DraftSkuAttributesEditor, { props: { skuId: 'black', sku, targetKey: 'mercadolibre:cbt' } })
    await wrapper.findAll('button').find(button => button.text() === '增加自定义规格')!.trigger('click')
    await wrapper.get('input[aria-label="自定义规格名称"]').setValue('Design')
    await wrapper.get('input[aria-label="自定义规格值"]').setValue('Neck protection')
    expect(catalog.currentDraft.skuItems[0]?.custom_attributes_by_target).toEqual({ 'mercadolibre:cbt': [{ name: 'Design', value: 'Neck protection' }] })
    expect(catalog.currentDraft.skuItems[1]?.custom_attributes_by_target).toBeUndefined()
    expect(catalog.currentDraft.skuItems[0]?.attributes_by_target).toEqual({})
    await wrapper.findAll('button').find(button => button.text() === '删除规格')!.trigger('click')
    expect(catalog.currentDraft.skuItems[0]?.custom_attributes_by_target?.['mercadolibre:cbt']).toEqual([])
    wrapper.unmount()
  })

  it('单项入口只填写当前 SKU，批量入口位于列表上方' , async () => {
    const { editor } = setup()
    const fill = vi.spyOn(editor.actions, 'fillAttributesByAi').mockResolvedValue({ filledCount: 1, needReview: [], warning: '' })
    const wrapper = mount(DraftSkuAttributesEditor, { props: { skuId: 'black', sku, targetKey: 'ozon:global' } })
    expect(wrapper.text()).not.toContain('AI 补齐该市场已选 SKU')
    await wrapper.findAll('button').find(button => button.text() === 'AI 补齐此 SKU 属性')!.trigger('click')
    await flushPromises()
    expect(fill.mock.calls).toEqual([['black']])
    expect(wrapper.text()).toContain('新增 1 项')
    wrapper.unmount()
  })
})
