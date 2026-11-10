import { reactive, nextTick } from 'vue'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import ProductSkuEditor from '../ProductSkuEditor.vue'
import type { ImageAsset, ProductSku } from '@/types/workflow'

enableAutoUnmount(afterEach)

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

describe('商品 SKU 图片展示', () => {
  const images = [
    { id: '图一', previewUrl: '/preview/one.jpg', url: '/one.jpg' },
    { id: '图二', previewUrl: '', url: '/two.jpg' },
  ] as ImageAsset[]

  it('未展开详情也按 SKU 显示图片，修改选图后立即更新且不影响其他规格', async () => {
    const { rows, wrapper } = setup()
    rows[0].image_asset_id = '图一'
    rows[1].image_asset_id = '图二'
    await wrapper.setProps({ images })
    expect(wrapper.get('#product-sku-a img').attributes('src')).toBe('/preview/one.jpg')
    expect(wrapper.get('#product-sku-b img').attributes('src')).toBe('/two.jpg')
    expect(wrapper.get('#product-sku-c').text()).toContain('未设置图片')
    await wrapper.get('button[aria-label="编辑 a 详情"]').trigger('click')
    await wrapper.get('button[aria-label="选择a 的图片"]').trigger('click')
    await flushPromises()
    const choose = document.body.querySelector<HTMLButtonElement>('button[aria-label="使用图片 图二"]')!
    choose.click()
    await nextTick()
    expect(rows[0].image_asset_id).toBe('图二')
    expect(wrapper.get('#product-sku-a img').attributes('src')).toBe('/two.jpg')
    expect(rows[1].image_asset_id).toBe('图二')
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
  })

  it('缺失关联和加载失败显示提示，素材更新后显示新图', async () => {
    const { rows, wrapper } = setup()
    rows[0].image_asset_id = '不存在'
    rows[1].image_asset_id = '图二'
    await wrapper.setProps({ images })
    expect(wrapper.get('#product-sku-a').text()).toContain('图片不可用')
    await wrapper.get('#product-sku-b img').trigger('error')
    expect(wrapper.get('#product-sku-b').text()).toContain('图片不可用')
    await wrapper.setProps({ images: [{ ...images[1], url: '/new.jpg' }] })
    expect(wrapper.get('#product-sku-b img').attributes('src')).toBe('/new.jpg')
  })

  it('只读时仍可放大查看，预览内拖出不关闭，遮罩点击正常关闭且不修改资料', async () => {
    const rows = [{ id: 'a', name: '规格甲', image_asset_id: '图一', options: {}, package_dimensions: {} }] as ProductSku[]
    const wrapper = mount(ProductSkuEditor, { props: { modelValue: rows, images, disabled: true }, global: { stubs: { Teleport: true } } })
    const before = JSON.stringify(rows)
    await wrapper.get('button[aria-label="预览 规格甲 的图片"]').trigger('click')
    await flushPromises()
    const dialog = wrapper.get('dialog')
    expect((dialog.element as HTMLDialogElement).open).toBe(true)
    const image = dialog.get('img')
    expect(image.attributes('src')).toBe('/preview/one.jpg')
    const pointer = { button: 0, pointerId: 1, isPrimary: true }
    await image.trigger('pointerdown', pointer)
    await dialog.trigger('pointerup', pointer)
    expect((dialog.element as HTMLDialogElement).open).toBe(true)
    await dialog.trigger('pointerdown', pointer)
    await dialog.trigger('pointerup', pointer)
    await flushPromises()
    expect(wrapper.findComponent({ name: 'WorkspaceDialog' }).props('open')).toBe(false)
    expect((wrapper.get('dialog').element as HTMLDialogElement).open).toBe(false)
    expect(JSON.stringify(rows)).toBe(before)
  })
})
