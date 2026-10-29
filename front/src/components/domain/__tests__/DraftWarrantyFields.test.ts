// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { reactive } from 'vue'
import { describe, expect, it } from 'vitest'
import DraftWarrantyFields from '@/components/domain/DraftWarrantyFields.vue'
import { createEmptyDraftDetail } from '@/constants/initialState'

const noWarranty = { id: 'WARRANTY_TYPE', value_id: '6150835', value_name: 'Sin garantía' }
function panelProps() {
  return { draft: reactive(createEmptyDraftDetail('mercadolibre')), issues: [] }
}

describe('Mercado 保修条款', () => {
  it.each(['ozon'] as const)('%s 不展示保修且不修改草稿', (platform) => {
    const draft = createEmptyDraftDetail(platform)
    const wrapper = mount(DraftWarrantyFields, { props: { draft, issues: [] } })
    expect(wrapper.find('[data-testid="draft-warranty-fields"]').exists()).toBe(false)
    expect(draft.saleTerms).toEqual([])
    expect(wrapper.emitted('invalidatePublishValidation')).toBeUndefined()
  })

  it('未配置时默认无保修，并写入实际将保存和发布的条款', () => {
    const props = panelProps()
    const wrapper = mount(DraftWarrantyFields, { props })
    expect((wrapper.get('select[data-publish-draft-field="warrantyType"]').element as HTMLSelectElement).value).toBe('none')
    expect(props.draft.saleTerms).toEqual([noWarranty])
    expect(wrapper.get('[data-publish-draft-field="warrantyDuration"]').attributes('disabled')).toBeDefined()
    expect(wrapper.get('[data-publish-draft-field="warrantyUnit"]').attributes('disabled')).toBeDefined()
    expect(wrapper.emitted('invalidatePublishValidation')).toHaveLength(1)
  })

  it('保留已有厂家保修和时长，不用默认值覆盖', () => {
    const props = panelProps()
    props.draft.saleTerms = [
      { id: 'WARRANTY_TYPE', value_id: '2230279', value_name: 'Garantía de fábrica' },
      { id: 'WARRANTY_TIME', value_name: '2 años', value_struct: { number: 2, unit: 'años' } },
    ]
    const wrapper = mount(DraftWarrantyFields, { props })
    expect((wrapper.get('[data-publish-draft-field="warrantyType"]').element as HTMLSelectElement).value).toBe('factory')
    expect((wrapper.get('[data-publish-draft-field="warrantyDuration"]').element as HTMLInputElement).value).toBe('2')
    expect((wrapper.get('[data-publish-draft-field="warrantyUnit"]').element as HTMLSelectElement).value).toBe('years')
    expect(wrapper.emitted('invalidatePublishValidation')).toBeUndefined()
  })

  it('允许修改保修和时长，切回无保修移除时长但保留其他销售条款', async () => {
    const props = panelProps()
    const otherTerm = { id: 'MANUFACTURING_TIME', value_name: '5 days' }
    props.draft.saleTerms = [otherTerm, noWarranty]
    const wrapper = mount(DraftWarrantyFields, { props })
    await wrapper.get('[data-publish-draft-field="warrantyType"]').setValue('seller')
    expect(props.draft.saleTerms).toContainEqual({ id: 'WARRANTY_TIME', value_name: '3 meses', value_struct: { number: 3, unit: 'meses' } })
    await wrapper.get('[data-publish-draft-field="warrantyDuration"]').setValue('6')
    await wrapper.get('[data-publish-draft-field="warrantyUnit"]').setValue('years')
    expect(props.draft.saleTerms).toContainEqual({ id: 'WARRANTY_TIME', value_name: '6 años', value_struct: { number: 6, unit: 'años' } })
    await wrapper.get('[data-publish-draft-field="warrantyType"]').setValue('none')
    expect(props.draft.saleTerms).toEqual([otherTerm, noWarranty])
    expect(wrapper.emitted('invalidatePublishValidation')).toHaveLength(4)
  })

  it('切换草稿会为新的 Mercado 草稿补默认值，处理任务期间不改资料', async () => {
    const props = panelProps()
    const wrapper = mount(DraftWarrantyFields, { props: { ...props, loading: true } })
    expect(props.draft.saleTerms).toEqual([])
    await wrapper.setProps({ loading: false })
    expect(props.draft.saleTerms).toEqual([noWarranty])
    const next = reactive(createEmptyDraftDetail('mercadolibre'))
    next.draftId = 'new-draft'
    await wrapper.setProps({ draft: next })
    expect(next.saleTerms).toEqual([noWarranty])
  })
})


describe('Yandex 保修期限', () => {
  it('展示独立设置，不自动填写承诺，编辑后可保存和回显', async () => {
    const draft = reactive(createEmptyDraftDetail('yandex'))
    const wrapper = mount(DraftWarrantyFields, { props: { draft, issues: [] } })
    expect(wrapper.find('[data-testid="yandex-warranty-fields"]').exists()).toBe(true)
    expect(draft.guaranteePeriod).toEqual({})
    expect(draft.saleTerms).toEqual([])
    expect(wrapper.emitted('invalidatePublishValidation')).toBeUndefined()
    await wrapper.get('[aria-label="Yandex 保修时长"]').setValue('12')
    await wrapper.get('[aria-label="Yandex 保修单位"]').setValue('MONTH')
    await wrapper.get('[aria-label="Yandex 保修说明"]').setValue('Гарантия продавца')
    expect(draft.guaranteePeriod).toEqual({ timePeriod: '12', timeUnit: 'MONTH', comment: 'Гарантия продавца' })
    expect(wrapper.emitted('invalidatePublishValidation')).toHaveLength(3)
    const next = reactive(createEmptyDraftDetail('yandex'))
    next.guaranteePeriod = { timePeriod: 2, timeUnit: 'YEAR' }
    await wrapper.setProps({ draft: next, loading: true })
    expect((wrapper.get('[aria-label="Yandex 保修时长"]').element as HTMLInputElement).value).toBe('2')
    expect(wrapper.get('fieldset').attributes('disabled')).toBeDefined()
  })
})
