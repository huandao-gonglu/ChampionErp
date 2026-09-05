// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { reactive } from 'vue'
import { describe, expect, it } from 'vitest'
import DraftSkuPanel from '@/components/domain/DraftSkuPanel.vue'
import { createEmptyDraftDetail } from '@/constants/initialState'
import type { ProductSku } from '@/types/workflow'

const facts: ProductSku[] = [{
  id: 'red-s', source_sku_id: 'supplier-red-s', name: '红色 S', options: { 颜色: '红', 尺码: 'S' },
  cost_cny: '20', supplier_stock: '500', image: '', barcode: '', active: true, source_snapshot: {},
  package_dimensions: { length_cm: '10', width_cm: '12', height_cm: '14', weight_kg: '0.5' },
}, {
  id: 'blue-l', source_sku_id: 'supplier-blue-l', name: '蓝色 L', options: { 颜色: '蓝', 尺码: 'L' },
  cost_cny: '40', supplier_stock: '900', image: '', barcode: '', active: false, source_snapshot: {},
  package_dimensions: { length_cm: '20', width_cm: '24', height_cm: '28', weight_kg: '1' },
}]

describe('草稿 SKU 选品', () => {
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
})
