// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { reactive } from 'vue'
import { describe, expect, it } from 'vitest'
import DraftSkuPanel from '@/components/domain/DraftSkuPanel.vue'
import SkuImagePicker from '@/components/domain/SkuImagePicker.vue'
import { createEmptyDraftDetail } from '@/constants/initialState'
import type { ProductSku } from '@/types/workflow'

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
  it('新增和库存为空的 SKU 默认填写供应商库存，保留手动库存', async () => {
    const draft = reactive(createEmptyDraftDetail('ozon'))
    draft.draftId = 'draft-stock'
    draft.skuItems = [{ sku_id: 'red-s', sku: 'SELL-RED', selected: true, stock: '', overrides: {}, attributes_by_target: {}, pricing: {}, publications: {} }]
    const wrapper = mount(DraftSkuPanel, { props: { draft, skus: facts, loading: false } })
    const stocks = wrapper.findAll<HTMLInputElement>('input[aria-label="可售库存"]')
    expect(stocks.map(input => input.element.value)).toEqual(['500', '900'])
    await stocks[0]!.setValue('12')
    await stocks[1]!.setValue('0')
    await wrapper.setProps({ skus: facts.map(sku => ({ ...sku, supplier_stock: '1000' })) })
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

  it('恢复已保存的选品并列出新增规格，全选不会启用来源停用规格', async () => {
    const draft = reactive(createEmptyDraftDetail('ozon'))
    draft.draftId = 'draft-one'
    draft.skuItems = [{ sku_id: 'red-s', sku: 'SELL-RED', selected: true, stock: '0', overrides: {}, attributes_by_target: {}, pricing: {}, publications: {} }]
    const wrapper = mount(DraftSkuPanel, { props: { draft, skus: facts, loading: false } })
    expect(draft.skuItems).toHaveLength(2)
    expect(draft.skuItems[0]?.selected).toBe(true)
    expect(wrapper.findAll('input[type="checkbox"]')[1]?.attributes('disabled')).toBeDefined()
    expect(wrapper.get('input[aria-label="可售库存"]').element).toHaveProperty('value', '0')
    await wrapper.findAll('button').find(button => button.text() === '全选启用规格')!.trigger('click')
    expect(draft.skuItems.map(row => row.selected)).toEqual([true, false])
    expect(facts[1]?.active).toBe(false)
  })

  it('草稿修改成本不改商品事实，并立即使已应用售价失效', async () => {
    const draft = reactive(createEmptyDraftDetail('ozon'))
    draft.draftId = 'draft-two'
    draft.skuItems = [{ sku_id: 'red-s', sku: 'SELL-RED', selected: true, stock: '1', overrides: {}, attributes_by_target: {}, pricing: { applied: true }, publications: {} }]
    const wrapper = mount(DraftSkuPanel, { props: { draft, skus: facts.slice(0, 1), loading: false } })
    await wrapper.findAll('button').find(button => button.text() === '详情')!.trigger('click')
    const cost = wrapper.findAll('label').find(label => label.text().startsWith('采购成本 CNY'))!.get('input')
    await cost.setValue('18')
    expect(draft.skuItems[0]?.overrides.cost_cny).toBe('18')
    expect(draft.skuItems[0]?.pricing.applied).toBe(false)
    expect(facts[0]?.cost_cny).toBe('20')
  })

  it('从图片池换图与恢复默认图都不改商品事实或已应用核价', async () => {
    const draft = reactive(createEmptyDraftDetail('ozon'))
    draft.draftId = 'draft-images'
    draft.skuItems = [{ sku_id: 'red-s', sku: 'SELL-RED', selected: true, stock: '1', overrides: {}, attributes_by_target: {}, pricing: { applied: true }, publications: {} }]
    const skus = [{ ...facts[0]!, image_asset_id: 'original' }]
    const wrapper = mount(DraftSkuPanel, { props: { draft, skus, loading: false } })
    await wrapper.findAll('button').find(button => button.text() === '详情')!.trigger('click')
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
