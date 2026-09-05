// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { reactive } from 'vue'
import { describe, expect, it } from 'vitest'
import ProductEditorPanel from '@/components/domain/ProductEditorPanel.vue'
import { normalizeBackendProduct, PRODUCT_SCHEMA_VERSION, toBackendProduct } from '@/api/workflow/normalizers'

function product(id = 'product-1') {
  return reactive(normalizeBackendProduct({
    schema_version: PRODUCT_SCHEMA_VERSION,
    product_id: id,
    source: { title: '便携风扇', attributes: { 电机类型: '无刷电机', 电池容量: '2000mAh-4000mAh（含）', 尺寸: '0' } },
  }))
}

describe('商品来源属性编辑', () => {
  it('AI 写入的主档属性显示在商品补充属性，编辑保存后可重新读取', async () => {
    const current = reactive(normalizeBackendProduct({
      schema_version: PRODUCT_SCHEMA_VERSION,
      product_id: '30c04c33c40d7f05',
      attributes: { 适用年龄: '1-99岁' },
      source: { title: '木质狗屋', attributes: { 材质: '木质' } },
    }))
    const wrapper = mount(ProductEditorPanel, { props: { product: current, loading: false } })
    const profile = wrapper.get('section[aria-label="商品补充属性"]')
    expect(profile.get<HTMLInputElement>('input:not([type="search"])').element.value).toBe('适用年龄')
    expect(profile.get('textarea').element.value).toBe('1-99岁')
    await profile.get('textarea').setValue('2-99岁')
    const reloaded = normalizeBackendProduct(toBackendProduct(current))
    expect(reloaded.attributes).toEqual({ 适用年龄: '2-99岁' })
    expect(reloaded.source.attributes).toEqual({ 材质: '木质' })
    await profile.get('button[aria-label="删除属性 适用年龄"]').trigger('click')
    expect(normalizeBackendProduct(toBackendProduct(current)).attributes).toEqual({})
  })

  it('两个属性区分别校验，修改一个属性不会把其他 JSON 值转换为文本', async () => {
    const current = product()
    current.attributes = { 适用年龄: '1-99岁', 是否带电: false, 配件: ['门', '窗'], 包装: { 数量: 2 } }
    current.source.attributes = { 颜色: ['原木', '红松'], 规格数: 4 }
    const wrapper = mount(ProductEditorPanel, { props: { product: current, loading: false } })
    const profile = wrapper.get('section[aria-label="商品补充属性"]')
    const source = wrapper.get('section[aria-label="来源产品属性"]')
    await profile.get('textarea').setValue('2-99岁')
    const wire = toBackendProduct(current)
    expect(wire.attributes).toEqual({ 适用年龄: '2-99岁', 是否带电: false, 配件: ['门', '窗'], 包装: { 数量: 2 } })
    expect(wire.source!.attributes).toEqual({ 颜色: ['原木', '红松'], 规格数: 4 })
    await profile.findAll('button').find((button) => button.text() === '添加属性')!.trigger('click')
    await source.get('textarea').setValue('白色')
    expect(wrapper.get('button.btn-primary').attributes('disabled')).toBeDefined()
    await profile.get('button[aria-label="删除属性 未命名"]').trigger('click')
    expect(wrapper.get('button.btn-primary').attributes('disabled')).toBeUndefined()
  })

  it('编辑、重命名、增删来源属性后，经商品保存协议重新读取仍保留完整值', async () => {
    const current = product()
    const wrapper = mount(ProductEditorPanel, { props: { product: current, loading: false } })
    const rows = () => wrapper.get('section[aria-label="来源产品属性"]').findAll('[data-testid="product-attribute-row"]')
    expect(rows()).toHaveLength(3)
    expect(rows()[1]!.get('textarea').element.value).toBe('2000mAh-4000mAh（含）')
    await rows()[0]!.get('input').setValue('电机')
    await rows()[0]!.get('textarea').setValue('已核实的无刷电机')
    await rows()[2]!.get('button').trigger('click')
    await wrapper.get('section[aria-label="来源产品属性"]').findAll('button').find((button) => button.text() === '添加属性')!.trigger('click')
    expect(wrapper.get('button.btn-primary').attributes('disabled')).toBeDefined()
    await rows()[2]!.get('input').setValue('风速档位')
    await rows()[2]!.get('textarea').setValue('5档')
    await wrapper.get('button.btn-primary').trigger('click')
    expect(wrapper.emitted('save')).toHaveLength(1)
    const reloaded = normalizeBackendProduct(toBackendProduct(current))
    expect(reloaded.source.attributes).toEqual({ 电机: '已核实的无刷电机', 电池容量: '2000mAh-4000mAh（含）', 风速档位: '5档' })
  })

  it('重名不能覆盖已有值，修正后恢复保存；清空全部属性不会恢复旧值', async () => {
    const current = product()
    const wrapper = mount(ProductEditorPanel, { props: { product: current, loading: false } })
    const rows = () => wrapper.get('section[aria-label="来源产品属性"]').findAll('[data-testid="product-attribute-row"]')
    await rows()[0]!.get('input').setValue(' 电池容量 ')
    expect(wrapper.get('[role="alert"]').text()).toContain('重复')
    expect(wrapper.get('button.btn-primary').attributes('disabled')).toBeDefined()
    expect(current.source.attributes.电池容量).toBe('2000mAh-4000mAh（含）')
    await rows()[0]!.get('input').setValue('电机类型')
    expect(wrapper.get('button.btn-primary').attributes('disabled')).toBeUndefined()
    while (rows().length) await rows()[0]!.get('button').trigger('click')
    expect(normalizeBackendProduct(toBackendProduct(current)).source.attributes).toEqual({})
    expect(wrapper.text()).toContain('暂无来源属性')
  })

  it('全部展示类目专属属性，搜索只筛选显示，切换商品清除未完成编辑', async () => {
    const current = product()
    current.source.attributes = Object.fromEntries(Array.from({ length: 70 }, (_, index) => [`属性${index}`, `值${index}`]))
    const wrapper = mount(ProductEditorPanel, { props: { product: current, loading: false } })
    expect(wrapper.findAll('[data-testid="product-attribute-row"]')).toHaveLength(70)
    await wrapper.get('input[type="search"]').setValue('值69')
    expect(wrapper.findAll('[data-testid="product-attribute-row"]')).toHaveLength(1)
    expect(Object.keys(toBackendProduct(current).source!.attributes!)).toHaveLength(70)
    await wrapper.get('section[aria-label="来源产品属性"]').findAll('button').find((button) => button.text() === '添加属性')!.trigger('click')
    expect(wrapper.get('button.btn-primary').attributes('disabled')).toBeDefined()
    await wrapper.setProps({ product: product('product-2') })
    expect(wrapper.get('button.btn-primary').attributes('disabled')).toBeUndefined()
    expect(wrapper.findAll('[data-testid="product-attribute-row"]')).toHaveLength(3)
  })
})
