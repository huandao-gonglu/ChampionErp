// @vitest-environment jsdom
import { enableAutoUnmount, mount } from '@vue/test-utils'
import { reactive } from 'vue'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import DraftSkuPanel from '@/components/domain/DraftSkuPanel.vue'
import SkuImagePicker from '@/components/domain/SkuImagePicker.vue'
import { createEmptyDraftDetail } from '@/constants/initialState'
import type { ProductSku } from '@/types/workflow'

enableAutoUnmount(afterEach)

const facts: ProductSku[] = [{
  id: 'red-s', source_sku_id: 'supplier-red-s', name: '红色 S', options: { 颜色: '红', 尺码: 'S' },
  cost_cny: '20', supplier_stock: '500', image_asset_id: '', barcode: '', active: true, source_snapshot: {},
  package_dimensions: { length_cm: '10', width_cm: '12', height_cm: '14', weight_kg: '0.5' },
}, {
  id: 'blue-l', source_sku_id: 'supplier-blue-l', name: '蓝色 L', options: { 颜色: '蓝', 尺码: 'L' },
  cost_cny: '40', supplier_stock: '900', image_asset_id: '', barcode: '', active: false, source_snapshot: {},
  package_dimensions: { length_cm: '20', width_cm: '24', height_cm: '28', weight_kg: '1' },
}]

describe('草稿 SKU 选品', () => {
  beforeEach(() => setActivePinia(createPinia()))
  it('新增和库存为空的 SKU 默认填写供应商库存，保留手动库存', async () => {
    const draft = reactive(createEmptyDraftDetail('ozon'))
    draft.draftId = 'draft-stock'
    draft.skuItems = [{ sku_id: 'red-s', sku: 'SELL-RED', selected: true, stock: '', overrides: {}, attributes_by_target: {}, pricing: {}, publications: {} }]
    const skus = facts.map(sku => ({ ...sku, active: true }))
    const wrapper = mount(DraftSkuPanel, { props: { draft, skus, loading: false } })
    const stocks = wrapper.findAll<HTMLInputElement>('input[aria-label="可售库存"]')
    expect(stocks.map(input => input.element.value)).toEqual(['500', '900'])
    await stocks[0]!.setValue('12')
    await stocks[1]!.setValue('0')
    await wrapper.setProps({ skus: skus.map(sku => ({ ...sku, supplier_stock: '1000' })) })
    expect(stocks.map(input => input.element.value)).toEqual(['12', '0'])
    expect(facts.map(sku => sku.supplier_stock)).toEqual(['500', '900'])
  })

  it('供应商库存缺失时保持空白，资料补齐后填入零库存', async () => {
    const draft = reactive(createEmptyDraftDetail('ozon'))
    const wrapper = mount(DraftSkuPanel, { props: { draft, skus: [{ ...facts[0]!, supplier_stock: '' }], loading: false } })
    expect(draft.skuItems[0]!.stock).toBe('')
    await wrapper.setProps({ skus: [{ ...facts[0]!, supplier_stock: '0' }] })
    expect(wrapper.get<HTMLInputElement>('input[aria-label="可售库存"]').element.value).toBe('0')
  })

  it('恢复已保存的选品，停用规格不补入草稿', async () => {
    const draft = reactive(createEmptyDraftDetail('ozon'))
    draft.draftId = 'draft-one'
    draft.skuItems = [{ sku_id: 'red-s', sku: 'SELL-RED', selected: true, stock: '0', overrides: {}, attributes_by_target: {}, pricing: {}, publications: {} }]
    const wrapper = mount(DraftSkuPanel, { props: { draft, skus: facts, loading: false } })
    expect(draft.skuItems).toHaveLength(1)
    expect(draft.skuItems[0]?.selected).toBe(true)
    expect(wrapper.find('input[aria-label="发布 蓝色 L"]').exists()).toBe(false)
    expect(wrapper.get('input[aria-label="可售库存"]').element).toHaveProperty('value', '0')
    await wrapper.findAll('button').find(button => button.text() === '全选启用规格')!.trigger('click')
    expect(draft.skuItems.map(row => row.selected)).toEqual([true])
    expect(facts[1]?.active).toBe(false)
  })

  it('新增启用规格默认全选，父复选框支持半选且始终跳过停用规格', async () => {
    const draft = reactive(createEmptyDraftDetail('ozon'))
    const skus = [facts[0]!, { ...facts[0]!, id: 'green', name: '绿色 S' }, facts[1]!]
    const wrapper = mount(DraftSkuPanel, { props: { draft, skus, loading: false } })
    const parent = wrapper.get<HTMLInputElement>('input[aria-label="全选启用 SKU"]')
    expect(draft.skuItems.map(row => row.selected)).toEqual([true, true])
    expect(parent.element.checked).toBe(true)
    expect(parent.element.indeterminate).toBe(false)
    expect(wrapper.text()).toContain('已选择 2 / 2')

    await wrapper.get('input[aria-label="发布 红色 S"]').setValue(false)
    expect(parent.element.checked).toBe(false)
    expect(parent.element.indeterminate).toBe(true)
    await parent.setValue(true)
    expect(draft.skuItems.map(row => row.selected)).toEqual([true, true])
    await parent.setValue(false)
    expect(draft.skuItems.every(row => !row.selected)).toBe(true)
    expect(parent.element.indeterminate).toBe(false)

    await wrapper.setProps({ skus: [...skus, { ...facts[0]!, id: 'new', name: '新增规格' }] })
    expect(draft.skuItems.map(row => row.selected)).toEqual([false, false, true])
    expect(parent.element.indeterminate).toBe(true)
    await wrapper.setProps({ loading: true })
    expect(parent.attributes('disabled')).toBeDefined()
  })

  it('只有停用规格时不加入草稿并显示空状态', () => {
    const draft = reactive(createEmptyDraftDetail('ozon'))
    const wrapper = mount(DraftSkuPanel, { props: { draft, skus: [facts[1]!], loading: false } })
    expect(draft.skuItems).toHaveLength(0)
    expect(wrapper.find('table').exists()).toBe(false)
    expect(wrapper.text()).toContain('暂无启用的 SKU')
    expect(wrapper.text()).not.toContain('已停用')
  })

  it('已有停用规格静默隐藏并取消发布，保留历史回执；重新启用后恢复显示但不自动勾选', async () => {
    const draft = reactive(createEmptyDraftDetail('ozon'))
    const publications = { 'ozon:global': { status: 'published', external_id: 'remote-blue' } }
    draft.skuItems = [{ sku_id: 'blue-l', sku: 'SELL-BLUE', selected: true, stock: '12', overrides: {}, attributes_by_target: {}, pricing: {}, publications }]
    const wrapper = mount(DraftSkuPanel, { props: { draft, skus: facts, loading: false } })
    expect(wrapper.find('input[aria-label="发布 蓝色 L"]').exists()).toBe(false)
    expect(draft.skuItems[0]!.selected).toBe(false)
    expect(draft.skuItems[0]!.publications).toEqual(publications)
    await wrapper.setProps({ skus: facts.map(sku => ({ ...sku, active: true })) })
    expect(wrapper.get<HTMLInputElement>('input[aria-label="发布 蓝色 L"]').element.checked).toBe(false)
    expect(draft.skuItems[0]!.stock).toBe('12')
    await wrapper.get('input[aria-label="发布 蓝色 L"]').setValue(true)
    await wrapper.setProps({ skus: facts })
    expect(wrapper.find('input[aria-label="发布 蓝色 L"]').exists()).toBe(false)
    expect(draft.skuItems[0]!.selected).toBe(false)
    expect(draft.skuItems[0]!.publications).toEqual(publications)
  })

  it('草稿只读显示成本并定位商品 SKU 编辑', async () => {
    const draft = reactive(createEmptyDraftDetail('ozon'))
    draft.draftId = 'draft-two'
    draft.skuItems = [{ sku_id: 'red-s', sku: 'SELL-RED', selected: true, stock: '1', overrides: {}, attributes_by_target: {}, pricing: { applied: true }, publications: {} }]
    const wrapper = mount(DraftSkuPanel, { props: { draft, skus: facts.slice(0, 1), loading: false } })
    await wrapper.findAll('button').find(button => button.text() === '属性 / 详情')!.trigger('click')
    await wrapper.findAll('button').find(button => button.text().includes('去商品修改'))!.trigger('click')
    expect(wrapper.emitted('editProduct')).toEqual([['red-s']])
    expect(draft.skuItems[0]?.overrides).toEqual({})
    expect(draft.skuItems[0]?.pricing.applied).toBe(true)
    expect(facts[0]?.cost_cny).toBe('20')
  })

  it('从图片池换图与恢复默认图都不改商品事实或已应用核价', async () => {
    const draft = reactive(createEmptyDraftDetail('ozon'))
    draft.draftId = 'draft-images'
    draft.skuItems = [{ sku_id: 'red-s', sku: 'SELL-RED', selected: true, stock: '1', overrides: {}, attributes_by_target: {}, pricing: { applied: true }, publications: {} }]
    const skus = [{ ...facts[0]!, image_asset_id: 'original' }]
    const wrapper = mount(DraftSkuPanel, { props: { draft, skus, loading: false } })
    await wrapper.findAll('button').find(button => button.text() === '属性 / 详情')!.trigger('click')
    const picker = wrapper.getComponent(SkuImagePicker)
    picker.vm.$emit('update:modelValue', 'translated')
    await wrapper.vm.$nextTick()
    expect(draft.skuItems[0]!.overrides.image_asset_id).toBe('translated')
    expect(draft.skuItems[0]!.pricing.applied).toBe(true)
    expect(skus[0]!.image_asset_id).toBe('original')
    picker.vm.$emit('inherit')
    await wrapper.vm.$nextTick()
    expect(draft.skuItems[0]!.overrides).not.toHaveProperty('image_asset_id')
    expect(picker.props('modelValue')).toBe('original')
    expect(draft.skuItems[0]!.pricing.applied).toBe(true)
  })
})
