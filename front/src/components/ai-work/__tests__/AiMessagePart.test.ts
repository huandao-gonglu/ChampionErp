import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import type { AiUiPart } from '@/types/aiWork'
import AiMessagePart from '../AiMessagePart.vue'

function mountPart(part: Record<string, unknown>) {
  return mount(AiMessagePart, {
    props: { part: part as AiUiPart },
  })
}

describe('AiMessagePart', () => {
  it('渲染 text 与默认折叠的 reasoning', () => {
    const text = mountPart({ type: 'text', text: '回答正文' })
    expect(text.get('[data-testid="ai-part-text"]').text()).toBe('回答正文')

    const reasoning = mountPart({
      type: 'reasoning',
      text: '分析过程',
      state: 'streaming',
    })
    expect(reasoning.get('[data-testid="ai-part-reasoning"] summary').text()).toBe('思考中…')
    expect(reasoning.text()).toContain('分析过程')
  })

  it('按状态渲染工具卡', () => {
    const wrapper = mountPart({
      type: 'tool-drafts_query',
      toolCallId: 'call-1',
      state: 'output-available',
      input: { scope: 'active' },
      output: { total: 2 },
    })

    const card = wrapper.get('[data-testid="ai-part-tool"]')
    expect(card.text()).toContain('drafts_query')
    expect(card.text()).toContain('工具完成')
    expect(card.text()).toContain('"total": 2')
  })

  it('global_task_start 历史 part 只按普通工具卡展示，不挂载交互任务卡', () => {
    const wrapper = mountPart({
      type: 'tool-global_task_start',
      toolCallId: 'call-task',
      state: 'output-available',
      input: { goal: '删除指定商品' },
      output: {
        ok: true,
        task_id: 'gtask-1',
        task: { task_id: 'gtask-1', goal: '删除指定商品', status: 'pending_approval' },
      },
    })

    expect(wrapper.get('[data-testid="ai-part-tool"]').text()).toContain('global_task_start')
    expect(wrapper.text()).toContain('工具完成')
    // 交互式任务卡只由 conversation 级 AiChatPanel 挂载，消息 part 不再重复渲染。
    expect(wrapper.find('[data-testid="global-task-card"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="global-task-approve"]').exists()).toBe(false)
  })

  it('开放的 global_task_start 展示受理语义，不误显示为审批', () => {
    const wrapper = mountPart({
      type: 'tool-global_task_start',
      toolCallId: 'call-task',
      state: 'input-available',
      input: { goal: '删除指定商品' },
    })

    const text = wrapper.get('[data-testid="ai-part-tool"]').text()
    expect(text).toContain('任务已受理 · 后台执行')
    expect(text).not.toContain('等待审批')
    expect(text).not.toContain('工具已就绪')
  })

  it('只为安全的 source 与 file URL 创建链接', () => {
    const source = mountPart({
      type: 'source-url',
      title: '官方来源',
      url: 'https://example.com/source',
    })
    expect(source.get('a').attributes('href')).toBe('https://example.com/source')

    const unsafeSource = mountPart({
      type: 'source-document',
      title: '不安全来源',
      url: 'javascript:alert(1)',
    })
    expect(unsafeSource.find('a').exists()).toBe(false)

    const safeFile = mountPart({
      type: 'file',
      filename: 'report.pdf',
      mediaType: 'application/pdf',
      url: 'data:application/pdf;base64,ZmFrZQ==',
    })
    expect(safeFile.get('a').attributes('href')).toContain('data:application/pdf;base64,')

    const unsafeFile = mountPart({
      type: 'file',
      filename: 'attack.pdf',
      mediaType: 'application/pdf',
      url: 'javascript:alert(1)',
    })
    expect(unsafeFile.find('a').exists()).toBe(false)
    expect(unsafeFile.text()).toContain('attack.pdf')
  })

  it('未知 part 折叠为调试信息且不影响消息', () => {
    const wrapper = mountPart({ type: 'data-custom', data: { value: 1 } })
    expect(wrapper.get('[data-testid="ai-part-debug"]').text()).toContain('data-custom')
    expect(wrapper.text()).toContain('"value": 1')
  })
})
