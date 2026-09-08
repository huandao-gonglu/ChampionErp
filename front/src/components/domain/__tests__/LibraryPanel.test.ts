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

describe('商品库按语言选择目标市场', () => {
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
})
