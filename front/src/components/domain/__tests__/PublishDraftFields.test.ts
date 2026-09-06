// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import PublishDraftFields from '@/components/domain/PublishDraftFields.vue'
import { createEmptyDraftDetail } from '@/constants/initialState'

function panelProps() {
  return { draft: createEmptyDraftDetail('ozon'), issues: [] }
}

describe('共享发布资料', () => {
  it('预检读取所选 SKU，不提供草稿级单品编码或包装编辑入口', () => {
    const wrapper = mount(PublishDraftFields, { props: panelProps() })
    expect(wrapper.find('[data-package-dimension-field]').exists()).toBe(false)
    expect(wrapper.find('[data-publish-draft-field="sku"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('逐 SKU 校验')
  })


  it('任一发布字段编辑都会通知父组件废弃旧预检与 Payload', async () => {
    const props = panelProps()
    props.draft.status = 'ready_to_publish'
    const wrapper = mount(PublishDraftFields, {
      props: {
        ...props,
      },
    })

    await wrapper.get('[data-publish-draft-field="allowGtinExemption"]').setValue(true)
    await wrapper.get('[data-publish-draft-field="warrantyType"]').setValue('seller')
    await wrapper.get('[data-publish-draft-field="warrantyDuration"]').setValue('6')
    await wrapper.get('[data-publish-draft-field="warrantyUnit"]').setValue('years')

    expect(wrapper.emitted('invalidatePublishValidation')).toHaveLength(4)
  })


  it('未配置保修条款时显示未选择，不把空数据伪装成无保修', () => {
    const props = panelProps()
    props.draft.saleTerms = []
    const wrapper = mount(PublishDraftFields, { props })

    const warrantyType = wrapper.get('[data-publish-draft-field="warrantyType"]')
    const warrantyDuration = wrapper.get('[data-publish-draft-field="warrantyDuration"]')
    const warrantyUnit = wrapper.get('[data-publish-draft-field="warrantyUnit"]')

    expect((warrantyType.element as HTMLSelectElement).value).toBe('')
    expect((warrantyDuration.element as HTMLInputElement).value).toBe('')
    expect((warrantyUnit.element as HTMLSelectElement).value).toBe('')
    expect(wrapper.text()).toContain('尚未选择保修类型')
    expect(warrantyDuration.attributes('disabled')).toBeDefined()
    expect(warrantyUnit.attributes('disabled')).toBeDefined()
    expect(props.draft.saleTerms).toEqual([])
  })


  it('明确选择无保修后才把 Mercado Libre 保修声明写入草稿', async () => {
    const props = panelProps()
    props.draft.saleTerms = []
    const wrapper = mount(PublishDraftFields, { props })

    await wrapper.get('[data-publish-draft-field="warrantyType"]').setValue('none')

    expect(props.draft.saleTerms).toEqual([
      { id: 'WARRANTY_TYPE', value_id: '6150835', value_name: 'Sin garantía' },
    ])
    expect(wrapper.emitted('invalidatePublishValidation')).toHaveLength(1)
    expect(wrapper.text()).toContain('已明确选择无保修')
  })


  it('选择卖家保修时把界面默认时长同时写入草稿，不留下仅显示的假默认', async () => {
    const props = panelProps()
    props.draft.saleTerms = []
    const wrapper = mount(PublishDraftFields, { props })

    await wrapper.get('[data-publish-draft-field="warrantyType"]').setValue('seller')

    expect(props.draft.saleTerms).toEqual([
      { id: 'WARRANTY_TYPE', value_id: '2230280', value_name: 'Garantía del vendedor' },
      {
        id: 'WARRANTY_TIME',
        value_name: '3 meses',
        value_struct: { number: 3, unit: 'meses' },
      },
    ])
    expect((wrapper.get('[data-publish-draft-field="warrantyDuration"]').element as HTMLInputElement).value).toBe('3')
    expect((wrapper.get('[data-publish-draft-field="warrantyUnit"]').element as HTMLSelectElement).value).toBe('months')
  })

})
