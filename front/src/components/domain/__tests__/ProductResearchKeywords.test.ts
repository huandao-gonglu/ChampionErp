import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import ProductResearchKeywords from '../ProductResearchKeywords.vue'
import { searchProductResearchKeywords } from '@/api/workflow/research'
import type { BackendProductResearchKeywordResponse } from '@/types/workflow'

vi.mock('@/api/workflow/research', () => ({ searchProductResearchKeywords: vi.fn() }))
enableAutoUnmount(afterEach)
const props = { keyword: 'organizer', marketId: 'amazon-us', disabled: false }
const result = (): BackendProductResearchKeywordResponse => ({
  ok: true, error: '', keyword: 'organizer', market_id: 'amazon-us', cached: false,
  fetched_at: '2026-10-11T12:00:00Z', expires_at: '2026-10-12T12:00:00Z',
  quota_receipts: [{ endpoint: 'KeywordQuery', domain: 1, request_consumed: 5, request_left: 95 }],
  items: Array.from({ length: 21 }, (_, i) => ({ keyword: `organizer ${i}`, chinese_name: `收纳 ${i}`,
    search_volume: i === 0 ? null : i * 100, rank: i + 1, rank_change: -i, data_updated_at: '2026-10-04' })),
})
beforeEach(() => { vi.clearAllMocks(); vi.mocked(searchProductResearchKeywords).mockResolvedValue(result()) })

describe('相关热搜词', () => {
  it('仅显式查询外发，翻批筛选排序与选词都不额外请求', async () => {
    const wrapper = mount(ProductResearchKeywords, { props })
    await wrapper.setProps({ keyword: ' kitchen ' })
    expect(searchProductResearchKeywords).not.toHaveBeenCalled()
    await wrapper.get('button').trigger('click')
    await flushPromises()
    expect(searchProductResearchKeywords).toHaveBeenCalledWith({ keyword: 'kitchen', market_id: 'amazon-us', refresh: false })
    expect(wrapper.text()).toContain('本次消耗 5 额度')
    expect(wrapper.findAll('li')[0].text()).toContain('organizer 20')
    await wrapper.get('select').setValue('source')
    expect(wrapper.text()).toContain('30 天搜索量 暂无')
    expect(wrapper.text()).toContain('下降 1')
    await wrapper.findAll('button').find(b => b.text() === '下一批')!.trigger('click')
    expect(wrapper.text()).toContain('organizer 10')
    await wrapper.get('select').setValue('volume')
    expect(wrapper.findAll('li')[0].text()).toContain('organizer 20')
    await wrapper.get('input').setValue('收纳 20')
    expect(wrapper.findAll('li')).toHaveLength(1)
    await wrapper.get('li button').trigger('click')
    expect(wrapper.emitted('select')).toEqual([['organizer 20']])
    expect(searchProductResearchKeywords).toHaveBeenCalledTimes(1)
  })

  it('缓存显示零扣量，重新获取才明确绕过缓存', async () => {
    vi.mocked(searchProductResearchKeywords).mockResolvedValueOnce({ ...result(), cached: true, quota_receipts: [] })
    const wrapper = mount(ProductResearchKeywords, { props })
    await wrapper.get('button').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('本次消耗 0 额度')
    expect(wrapper.text()).not.toContain('本次扣费未知')
    await wrapper.findAll('button').find(b => b.text().startsWith('重新获取'))!.trigger('click')
    await flushPromises()
    expect(searchProductResearchKeywords).toHaveBeenLastCalledWith({ market_id: props.marketId, keyword: props.keyword, refresh: true })
  })

  it('失败显示实际回执并清除旧候选，不自动重试', async () => {
    const wrapper = mount(ProductResearchKeywords, { props })
    await wrapper.get('button').trigger('click')
    await flushPromises()
    vi.mocked(searchProductResearchKeywords).mockResolvedValueOnce({ ...result(), ok: false, items: [], error: '额度不足' })
    await wrapper.get('button').trigger('click')
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toBe('额度不足')
    expect(wrapper.text()).toContain('本次消耗 5 额度')
    expect(wrapper.find('li').exists()).toBe(false)
    expect(searchProductResearchKeywords).toHaveBeenCalledTimes(2)
  })

  it('查询期间重复点击不外发，切换市场后丢弃旧结果', async () => {
    let resolve!: (value: BackendProductResearchKeywordResponse) => void
    vi.mocked(searchProductResearchKeywords).mockReturnValue(new Promise(r => { resolve = r }))
    const wrapper = mount(ProductResearchKeywords, { props })
    await wrapper.get('button').trigger('click')
    expect(wrapper.get('button').attributes('disabled')).toBeDefined()
    await wrapper.get('button').trigger('click')
    expect(searchProductResearchKeywords).toHaveBeenCalledTimes(1)
    await wrapper.setProps({ marketId: 'other-market' })
    resolve(result())
    await flushPromises()
    expect(wrapper.find('li').exists()).toBe(false)
  })

  it('未知扣费和无数据如实展示，禁用时不能触发请求', async () => {
    const wrapper = mount(ProductResearchKeywords, { props: { ...props, disabled: true } })
    await wrapper.get('button').trigger('click')
    expect(searchProductResearchKeywords).not.toHaveBeenCalled()
    await wrapper.setProps({ disabled: false })
    vi.mocked(searchProductResearchKeywords).mockResolvedValueOnce({ ...result(), ok: false, error: '扣费未知', items: [], quota_receipts: [] })
    await wrapper.get('button').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('本次扣费未知')
    vi.mocked(searchProductResearchKeywords).mockResolvedValueOnce({ ...result(), items: [] })
    await wrapper.get('button').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('暂无相关热搜词')
  })
})
