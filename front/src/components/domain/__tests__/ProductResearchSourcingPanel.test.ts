import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import ProductResearchSourcingPanel from '../ProductResearchSourcingPanel.vue'
import { importProductResearchSupplier, searchProductResearchSuppliers } from '@/api/workflow/research'
import type { HotProductCandidate, BackendProductResearchSourcing } from '@/types/workflow'

vi.mock('@/api/workflow/research', () => ({ importProductResearchSupplier: vi.fn(), searchProductResearchSuppliers: vi.fn() }))
enableAutoUnmount(afterEach)
const sourcing: BackendProductResearchSourcing = {
  query: { mode: 'keyword', keyword: '水槽下收纳架' }, status: 'completed',
  items: [{ product_id: '692505958039', title: '双层收纳架', source_url: 'https://detail.1688.com/offer/692505958039.html', image_url: '',
    price_cny: 13, store_name: '测试供应商', service_score: 4, monthly_sales: 100, min_order_quantity: 1, repurchase_rate: 12, collected_at: '2026-10-07' }],
}
const candidate: HotProductCandidate = { id: 'candidate-1', title: 'Under Sink Organizer', imageUrl: '', rank: 1,
  sourceUrl: 'https://www.amazon.com/dp/B0DNTL7T64', marketId: 'amazon-us', platform: 'amazon', site: 'amazon.com', keyword: 'organizer',
  rating: null, reviewCount: null, hotScore: 0, sourceName: 'Sorftime', collectedAt: '', raw: { sourcing },
}
beforeEach(() => vi.clearAllMocks())
describe('选品货源确认', () => {
  it('刷新后直接显示已保存货源，确认前不允许调用入库', async () => {
    vi.mocked(importProductResearchSupplier).mockResolvedValue({ ok: true, product_id: 'product-1', already_imported: false, quota_receipts: [] })
    const wrapper = mount(ProductResearchSourcingPanel, { props: { candidate, runId: 'run-1' } })
    expect(wrapper.text()).toContain('双层收纳架')
    expect(searchProductResearchSuppliers).not.toHaveBeenCalled()
    await wrapper.get('input[type="radio"]').setValue()
    const button = wrapper.findAll('button').find(item => item.text().startsWith('确认货源'))!
    expect(button.attributes('disabled')).toBeDefined()
    await button.trigger('click')
    expect(importProductResearchSupplier).not.toHaveBeenCalled()
    await wrapper.get('input[type="checkbox"]').setValue(true)
    await button.trigger('click')
    await flushPromises()
    expect(importProductResearchSupplier).toHaveBeenCalledWith({ run_id: 'run-1', candidate_id: 'candidate-1', supplier_id: '692505958039', confirmed: true })
    expect(wrapper.text()).toContain('已入库：product-1')
    expect(wrapper.emitted('imported')).toHaveLength(1)
  })
  it('新查询失败后不能沿用此前勾选的货源入库', async () => {
    vi.mocked(searchProductResearchSuppliers).mockRejectedValue(new Error('Sorftime：接口未开通'))
    const wrapper = mount(ProductResearchSourcingPanel, { props: { candidate, runId: 'run-1' } })
    await wrapper.get('input[type="radio"]').setValue()
    await wrapper.get('input[type="checkbox"]').setValue(true)
    await wrapper.findAll('button').find(item => item.text().startsWith('查找货源'))!.trigger('click')
    await flushPromises()
    expect(wrapper.find('input[type="checkbox"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('接口未开通')
    expect(importProductResearchSupplier).not.toHaveBeenCalled()
  })
})
