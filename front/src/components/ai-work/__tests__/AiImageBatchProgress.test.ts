import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import AiImageBatchProgress from '../AiImageBatchProgress.vue'
import { fetchImageBatch, type ImageBatchSnapshot } from '@/api/imageBatches'

vi.mock('@/api/imageBatches', () => ({ fetchImageBatch: vi.fn() }))
const fetchBatch = vi.mocked(fetchImageBatch)

function snapshot(id = 'conversation_batch'): ImageBatchSnapshot {
  return {
    draft_id: '',
    conversation_id: id, product_id: 'p', prompt: '翻译成俄语', created_at: '', updated_at: '',
    total: 4, completed: 2, success: 1, failed: 1, running: 1, queued: 1, terminal: false,
    items: ['success', 'failed', 'running', 'queued'].map((state, index) => ({
      index, source_id: String(index), source_url: `/file?path=source-${index}.png`,
      status: state as ImageBatchSnapshot['items'][number]['status'],
      result_urls: index === 0 ? ['/file?path=result.png'] : [],
      error: index === 1 ? '图片服务返回 HTTP 502。' : '', outcome_unknown: false,
      started_at: '', finished_at: '',
    })),
  }
}

beforeEach(() => { vi.useFakeTimers(); fetchBatch.mockReset() })
afterEach(() => { vi.clearAllTimers(); vi.useRealTimers() })

describe('图片批次展示', () => {
  it('恢复完整逐张记录，并在没有前台 Chat 时继续刷新未完成批次', async () => {
    fetchBatch.mockResolvedValue(snapshot())
    const wrapper = mount(AiImageBatchProgress, { props: { conversationId: 'conversation_batch' } })
    await flushPromises()
    expect(wrapper.text()).toContain('已处理 2 / 4 张')
    expect(wrapper.text()).toContain('成功 1 张 · 失败 1 张')
    expect(wrapper.text()).toContain('HTTP 502')
    expect(wrapper.findAll('li').map(row => row.attributes('data-status'))).toEqual(['success', 'failed', 'running', 'queued'])
    expect(wrapper.get('[data-status="queued"]').text()).toContain('○')
    expect(wrapper.get('[data-status="queued"]').text()).not.toContain('✓')
    expect(wrapper.get('a[href="/file?path=result.png"]').text()).toBe('查看结果图')
    await vi.advanceTimersByTimeAsync(1000)
    expect(fetchBatch).toHaveBeenCalledTimes(2)
    wrapper.unmount()
    await vi.advanceTimersByTimeAsync(3000)
    expect(fetchBatch).toHaveBeenCalledTimes(2)
  })

  it('完成批次停止轮询，切换会话时丢弃旧请求结果', async () => {
    let resolveOld!: (value: ImageBatchSnapshot) => void
    fetchBatch.mockReturnValueOnce(new Promise(resolve => { resolveOld = resolve }))
    const wrapper = mount(AiImageBatchProgress, { props: { conversationId: 'conversation_old' } })
    fetchBatch.mockResolvedValue({ ...snapshot('conversation_new'), terminal: true })
    await wrapper.setProps({ conversationId: 'conversation_new' })
    await flushPromises()
    resolveOld(snapshot('conversation_old'))
    await flushPromises()
    expect(wrapper.text()).toContain('处理结束')
    expect(wrapper.emitted('change')?.at(-1)?.[0]).toMatchObject({ conversation_id: 'conversation_new' })
    await vi.advanceTimersByTimeAsync(5000)
    expect(fetchBatch).toHaveBeenCalledTimes(2)
    wrapper.unmount()
  })

  it('不存在批次时只查询一次，危险图片链接不渲染', async () => {
    fetchBatch.mockResolvedValue(null)
    const wrapper = mount(AiImageBatchProgress, { props: { conversationId: 'conversation_other' } })
    await flushPromises()
    await vi.advanceTimersByTimeAsync(5000)
    expect(fetchBatch).toHaveBeenCalledTimes(1)
    const value = snapshot('conversation_new')
    value.items[0].source_url = 'javascript:alert(1)'
    value.items[0].result_urls = ['//evil.example/a', 'javascript:alert(1)']
    fetchBatch.mockResolvedValue(value)
    await wrapper.setProps({ conversationId: 'conversation_new' })
    await flushPromises()
    expect(wrapper.get('li').findAll('a')).toHaveLength(0)
    wrapper.unmount()
  })
})
