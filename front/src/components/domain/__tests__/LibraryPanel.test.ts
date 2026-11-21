import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import LibraryPanel from '../LibraryPanel.vue'
import type { MarketplaceOption, ProductIndexItem } from '@/types/workflow'

const platformOptions: MarketplaceOption[] = [
  { key: 'mercadolibre', label: '美客多', sites: [
    { key: 'CBT', code: 'CBT', label: '全局刊登', language: 'es' },
    { key: 'MLM', code: 'MLM', label: '墨西哥', language: 'es' },
    { key: 'MLC', code: 'MLC', label: '智利', language: 'es' },
    { key: 'MLB', code: 'MLB', label: '巴西', language: 'pt-BR' },
  ] },
  { key: 'yandex', label: 'Yandex', sites: [{ key: 'global', code: 'global', label: '俄罗斯', language: 'ru-RU' }] },
  { key: 'ozon', label: 'Ozon', sites: [{ key: 'global', code: 'global', label: '俄罗斯', language: 'ru-RU' }] },
]
const storeConfig = { mercadolibre: {
  listing_model: 'traditional_global_items',
  marketplace_bindings: ['MLM', 'MLC', 'MLB'].map(site_id => ({ site_id, logistic_type: 'remote', pricing_model: 'price' })),
} }
const items = ['商品一', '商品二'].map((title, index) => ({ productId: `product-${index + 1}`, title })) as ProductIndexItem[]

function mountPanel(selectedIds: string[] = []) {
  return mount(LibraryPanel, { props: { items, selectedIds, platformOptions, storeConfig, loading: false } })
}

describe('采集箱按语言选择目标市场', () => {
  it('后台刷新保留现有商品的编辑和勾选', async () => {
    const wrapper = mountPanel()
    await wrapper.setProps({ refreshing: true })
    expect(wrapper.findAll('button').find(button => button.text() === '刷新中…')!.element.disabled).toBe(true)
    await wrapper.get('button[title="编辑商品"]').trigger('click')
    expect(wrapper.emitted('edit')?.[0]).toEqual([items[0]])
    expect(wrapper.get('[aria-label="勾选商品"]').attributes('disabled')).toBeUndefined()
  })

  it('语言为父级，市场为子级，同语言跨平台归组且不展示 CBT', async () => {
    const wrapper = mountPanel()
    const spanish = wrapper.get('[data-language="es"]')
    const russian = wrapper.get('[data-language="ru-RU"]')
    expect(spanish.text()).toContain('墨西哥')
    expect(spanish.text()).toContain('智利')
    expect(spanish.text()).not.toContain('巴西')
    expect(russian.text()).toContain('Yandex')
    expect(russian.text()).toContain('Ozon')
    expect(wrapper.text()).not.toContain('全局刊登')
    await spanish.get('input').setValue(true)
    expect(spanish.findAll('input').every(input => (input.element as HTMLInputElement).checked)).toBe(true)
    expect(russian.findAll('input').every(input => !(input.element as HTMLInputElement).checked)).toBe(true)
    await spanish.findAll('input')[1].setValue(false)
    expect((spanish.get('input').element as HTMLInputElement).indeterminate).toBe(true)
    expect(wrapper.text()).toContain('已选 1 个语言、1 个市场')
  })

  it('批量按钮仅使用勾选商品，行按钮仅使用该行，两者共享市场选择', async () => {
    const wrapper = mountPanel(['product-1'])
    const batch = wrapper.get('[data-testid="library-claim-selected"]')
    expect(batch.attributes('disabled')).toBeDefined()
    await wrapper.get('[data-language="ru-RU"] input').setValue(true)
    await batch.trigger('click')
    await wrapper.findAll('[data-testid="library-claim-row"]')[1].trigger('click')
    const events = wrapper.emitted('claim')!
    expect(events[0][0]).toEqual(['product-1'])
    expect(events[1][0]).toEqual(['product-2'])
    expect(events[0][1]).toEqual(events[1][1])
    expect(events[0][1]).toEqual([
      expect.objectContaining({ platform: 'yandex', site: 'global', language: 'ru-RU' }),
      expect.objectContaining({ platform: 'ozon', site: 'global', language: 'ru-RU' }),
    ])
    await wrapper.setProps({ selectedIds: [] })
    expect(batch.attributes('disabled')).toBeDefined()
    expect(wrapper.get('[data-testid="library-claim-row"]').attributes('disabled')).toBeUndefined()
  })

  it('清空市场后禁用全部推送按钮，加载期间锁定市场勾选', async () => {
    const wrapper = mountPanel(['product-1'])
    await wrapper.get('[data-language="es"] input').setValue(true)
    await wrapper.setProps({ loading: true })
    expect(wrapper.findAll('[data-language] input').every(input => input.attributes('disabled') !== undefined)).toBe(true)
    await wrapper.setProps({ loading: false })
    await wrapper.findAll('button').find(button => button.text() === '清空选择')!.trigger('click')
    expect(wrapper.get('[data-testid="library-claim-selected"]').attributes('disabled')).toBeDefined()
    expect(wrapper.findAll('[data-testid="library-claim-row"]').every(button => button.attributes('disabled') !== undefined)).toBe(true)
  })

  it('支持按标题搜索、折叠目标市场并通过弹窗确认删除', async () => {
    const richItems = [
      { ...items[0], collectStatus: 'success', sourcePlatform: '1688', sourceUrl: 'https://detail.1688.com/offer/1.html' },
      { ...items[1], collectStatus: 'failed', sourcePlatform: 'generic', sourceUrl: 'https://example.com/2' },
    ] as ProductIndexItem[]
    const wrapper = mount(LibraryPanel, {
      props: { items: richItems, selectedIds: [], platformOptions, storeConfig, loading: false },
      global: { stubs: { Teleport: true } },
    })
    expect(wrapper.text()).toContain('已采集')
    expect(wrapper.text()).toContain('采集失败')

    await wrapper.get('input[type="search"]').setValue('商品二')
    expect(wrapper.findAll('tbody tr')).toHaveLength(1)
    expect(wrapper.get('tbody').text()).toContain('商品二')

    await wrapper.findAll('button').find(button => button.text().includes('收起'))!.trigger('click')
    expect(wrapper.get('[data-language="es"]').isVisible()).toBe(false)

    await wrapper.get('button[title="删除商品"]').trigger('click')
    await wrapper.findAll('dialog button').find(button => button.text().trim() === '确认删除')!.trigger('click')
    expect(wrapper.emitted('deleteItem')?.[0]).toEqual([richItems[1]])
  })

  it('操作下拉支持导出已勾选商品以及选择 JSON 文件导入商品', async () => {
    const wrapper = mountPanel(['product-1'])
    await wrapper.get('[data-testid="library-actions-trigger"]').trigger('click')
    const exportBtn = wrapper.get('[data-testid="library-action-export"]')
    expect(exportBtn.attributes('disabled')).toBeUndefined()
    await exportBtn.trigger('click')
    expect(wrapper.emitted('exportProducts')?.[0]).toEqual([['product-1']])

    const samplePayload = {
      format: 'champion-erp.library-products',
      version: 1,
      products: [{ name: '导入商品', source: { title: '导入商品' } }],
    }
    const file = new File([JSON.stringify(samplePayload)], 'products.json', { type: 'application/json' })
    Object.defineProperty(file, 'text', { value: async () => JSON.stringify(samplePayload) })
    const fileInput = wrapper.get<HTMLInputElement>('input[type="file"]')
    Object.defineProperty(fileInput.element, 'files', { value: [file], configurable: true })
    await fileInput.trigger('change')
    await new Promise(resolve => setTimeout(resolve, 0))
    expect(wrapper.emitted('importProducts')?.[0]).toEqual([samplePayload])
  })
})
