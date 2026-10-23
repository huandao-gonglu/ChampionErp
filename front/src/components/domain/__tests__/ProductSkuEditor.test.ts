import { reactive } from 'vue'
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import ProductSkuEditor from '../ProductSkuEditor.vue'
import type { ProductSku } from '@/types/workflow'

function setup() {
  const rows = reactive(['a', 'b', 'c'].map((id, i) => ({ id, name: id, cost_cny: String(i + 1), active: true,
    source_sku_id: '', supplier_stock: '9', options: {}, image_asset_id: '', barcode: '',
    package_dimensions: { weight_kg: String(i + 1) }, source_snapshot: {}, battery: false, liquid: false } as ProductSku)))
  return { rows, wrapper: mount(ProductSkuEditor, { props: { modelValue: rows, focusSkuId: 'b' } }) }
}

describe('商品 SKU 批量资料编辑', () => {
  it('定位指定 SKU 后只修改勾选范围，保留其他规格和重量', async () => {
    const { rows, wrapper } = setup()
    expect(wrapper.get<HTMLInputElement>('input[aria-label="批量选择 b"]').element.checked).toBe(true)
    await wrapper.get('input[aria-label="批量选择 a"]').setValue(true)
    await wrapper.get('input[aria-label="批量设置值"]').setValue('14')
    await wrapper.findAll('button').find(item => item.text().startsWith('应用到选中'))!.trigger('click')
    expect(rows.map(row => row.cost_cny)).toEqual(['14', '14', '3'])
    expect(rows.map(row => row.package_dimensions.weight_kg)).toEqual(['1', '2', '3'])
  })

  it('包装拒绝零值，布尔字段可批量明确设置', async () => {
    const { rows, wrapper } = setup()
    await wrapper.get('select[aria-label="批量修改字段"]').setValue('weight_kg')
    await wrapper.get('input[aria-label="批量设置值"]').setValue('0')
    expect(wrapper.findAll('button').find(item => item.text().startsWith('应用到选中'))!.attributes('disabled')).toBeDefined()
    await wrapper.get('select[aria-label="批量修改字段"]').setValue('battery')
    await wrapper.get('select[aria-label="批量设置值"]').setValue('true')
    await wrapper.findAll('button').find(item => item.text().startsWith('应用到选中'))!.trigger('click')
    expect(rows.map(row => row.battery)).toEqual([false, true, false])
  })
})
