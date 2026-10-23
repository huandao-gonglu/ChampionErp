// @vitest-environment jsdom
import { flushPromises, mount } from '@vue/test-utils'
import { reactive } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createEmptyDraftDetail } from '@/constants/initialState'
import type { CategorySelection, DraftSku, MarketplaceTargetSite, ProductSku } from '@/types/workflow'
import { DEFAULT_EMPTY_GTIN_REASON } from '@/utils/barcodePolicy'

const api = vi.hoisted(() => ({ fetchCategoryAttrs: vi.fn(), fetchCategoryAttributeValues: vi.fn() }))
vi.mock('@/api/workflow/publishing', () => api)
import DraftBarcodeFields from '@/components/domain/DraftBarcodeFields.vue'

function fixture() {
  const draft = reactive(createEmptyDraftDetail('mercadolibre'))
  draft.draftId = 'draft-1'
  draft.categoryId = 'CBT123'
  draft.skuItems = [{ sku_id: 'sku-1', selected: true }, { sku_id: 'sku-2', selected: true }] as DraftSku[]
  const target: MarketplaceTargetSite = { platform: 'mercadolibre', site: 'CBT', language: 'en-US', listingCurrency: 'USD' }
  const category: CategorySelection = {
    platform: 'mercadolibre', categoryId: 'CBT123', categoryPath: '商品', fetchedAt: '2026-10-23', raw: {},
    requiredAttributes: [], optionalAttributes: [
      { id: 'GTIN', name: 'GTIN', required: false },
      { id: 'EMPTY_GTIN_REASON', name: '缺失原因', required: false, valueMode: 'strict_enum', options: [DEFAULT_EMPTY_GTIN_REASON, 'The product is handmade'] },
    ],
  }
  const skus = [{ id: 'sku-1', barcode: '' }, { id: 'sku-2', barcode: '1234567890123' }] as ProductSku[]
  return { draft, target, category, skus }
}

beforeEach(() => vi.resetAllMocks())

describe('按市场显示条码资料', () => {
  it('不适用的市场或类目不显示条码，也不请求 Mercado 规则', async () => {
    const props = fixture()
    props.target.platform = 'yandex'
    props.category.platform = 'yandex'
    const wrapper = mount(DraftBarcodeFields, { props: { ...props, autoLoad: true } })
    expect(wrapper.find('fieldset').exists()).toBe(false)
    expect(api.fetchCategoryAttrs).not.toHaveBeenCalled()
    props.category.optionalAttributes = []
    await wrapper.setProps({ category: { ...props.category, platform: 'mercadolibre' }, target: { ...props.target, platform: 'mercadolibre' } })
    expect(wrapper.find('fieldset').exists()).toBe(false)
  })

  it('展示平台预填原因，允许修改和清空，重新打开仍保留明确清空', async () => {
    const props = fixture()
    props.draft.allowGtinExemption = true
    const wrapper = mount(DraftBarcodeFields, { props })
    expect(props.draft.attributes.EMPTY_GTIN_REASON).toBe(DEFAULT_EMPTY_GTIN_REASON)
    expect(wrapper.text()).toContain('已选 2 个 SKU，1 个未填写条码')
    await wrapper.get('select').setValue('The product is handmade')
    expect(props.draft.attributes.EMPTY_GTIN_REASON).toBe('The product is handmade')
    await wrapper.get('select').setValue('')
    expect(props.draft.attributes.EMPTY_GTIN_REASON).toBe('')
    wrapper.unmount()
    const reopened = mount(DraftBarcodeFields, { props })
    expect((reopened.get('select').element as HTMLSelectElement).value).toBe('')
    expect(props.draft.attributes.EMPTY_GTIN_REASON).toBe('')
    expect(reopened.emitted('change')).toBeUndefined()
  })

  it('全部有实际条码时不填写缺失原因', () => {
    const props = fixture()
    props.skus[0]!.barcode = '2345678901234'
    const wrapper = mount(DraftBarcodeFields, { props })
    expect(wrapper.find('select').exists()).toBe(false)
    expect(props.draft.attributes).not.toHaveProperty('EMPTY_GTIN_REASON')
  })

  it('平台允许自由文本时可以直接修改原因，不虚构平台默认选项', async () => {
    const props = fixture()
    props.category.optionalAttributes[1] = { id: 'EMPTY_GTIN_REASON', name: '缺失原因', required: false, valueMode: 'free_text' }
    const wrapper = mount(DraftBarcodeFields, { props })
    expect(props.draft.attributes).not.toHaveProperty('EMPTY_GTIN_REASON')
    await wrapper.get('input').setValue('商品为手工定制，无注册条码')
    expect(props.draft.attributes.EMPTY_GTIN_REASON).toBe('商品为手工定制，无注册条码')
  })

  it('初次显示即允许加载平台的后续选项', async () => {
    const props = fixture()
    props.category.optionalAttributes[1]!.hasMoreValues = true
    api.fetchCategoryAttributeValues.mockResolvedValue({ values: [{ id: 'custom', value: 'Another reason' }], hasMore: false, nextCursor: '' })
    const wrapper = mount(DraftBarcodeFields, { props })
    await wrapper.get('button').trigger('click')
    await flushPromises()
    expect(wrapper.findAll('option').map(option => option.text())).toContain('Another reason')
    await wrapper.get('select').setValue('Another reason')
    expect(props.draft.attributes.EMPTY_GTIN_REASON).toEqual({ values: [{ dictionaryValueId: 'custom', value: 'Another reason' }] })
  })

  it('切换目标后，旧请求不能给新目标预填原因', async () => {
    const props = fixture()
    let resolve!: (category: CategorySelection) => void
    api.fetchCategoryAttrs.mockReturnValue(new Promise<CategorySelection>(done => { resolve = done }))
    const wrapper = mount(DraftBarcodeFields, { props: { ...props, category: null, autoLoad: true } })
    expect(api.fetchCategoryAttrs).toHaveBeenCalledWith('mercadolibre', 'CBT123', 'CBT')
    await wrapper.setProps({ target: { ...props.target, platform: 'yandex' } })
    resolve(props.category)
    await flushPromises()
    expect(wrapper.find('fieldset').exists()).toBe(false)
    expect(props.draft.attributes).not.toHaveProperty('EMPTY_GTIN_REASON')
  })
})
