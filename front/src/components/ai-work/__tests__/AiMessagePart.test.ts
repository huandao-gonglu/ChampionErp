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
    expect(card.text()).toContain('查询草稿')
    expect(card.text()).toContain('已完成')
    expect(card.text()).toContain('"total": 2')
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

  it('原生已返回状态仍展示业务失败和部分完成', () => {
    const failed = mountPart({ type: 'tool-category_match', state: 'output-available',
      output: { ok: false, error: { message: '类目匹配超时' } } })
    expect(failed.text()).toContain('操作失败')
    const partial = mountPart({ type: 'tool-example_partial_result', state: 'output-available',
      output: { status: 'partial' } })
    expect(partial.text()).toContain('部分完成')
  })

  it.each(['Type', 'Syntax'])('执行前 %s 诊断保留在默认折叠的详情中', kind => {
    const errorText = `${kind} error in code:\nmain.py:20:21\nFix the errors and try again.`
    const wrapper = mountPart({
      type: 'dynamic-tool', toolName: 'run_code', state: 'output-error', errorText,
    })
    const details = wrapper.get<HTMLDetailsElement>('[data-testid="ai-tool-details"]')
    expect(details.element.open).toBe(false)
    expect(details.get('summary').text()).toContain('脚本检查未通过')
    expect(details.get('[data-testid="ai-script-diagnostic"]').text()).toBe(errorText)
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('操作失败')
    expect(wrapper.text()).not.toContain('已修复')
  })

  it.each([
    ['run_code', 'Runtime error:\nType error in code:\n写入中断', undefined, '操作失败'],
    ['run_code', '未知执行错误', undefined, '操作失败'],
    ['online_products_change', 'Type error in code:\n接口失败', undefined, '操作失败'],
    ['run_code', 'Type error in code:\n诊断', { ok: false, error: { message: '平台拒绝' } }, '操作失败'],
    ['run_code', 'Type error in code:\n诊断', { outcome_unknown: true }, '结果未确认'],
  ])('仅折叠已识别的执行前检查，不隐藏 %s 的其他错误：%s', (toolName, errorText, output, label) => {
    const wrapper = mountPart({ type: `tool-${toolName}`, state: 'output-error', errorText, output })
    const alert = wrapper.get('[role="alert"]')
    expect(alert.text()).toBe(errorText)
    expect(alert.element.closest('details')).toBeNull()
    expect(wrapper.get('summary').text()).toContain(label)
    expect(wrapper.find('[data-testid="ai-script-diagnostic"]').exists()).toBe(false)
  })

  it('历史导出的未返回工具不能被断言为等待审批', () => {
    const pending = mountPart({ type: 'tool-category_match', state: 'approval-requested' })
    expect(pending.text()).toContain('结果未确认')
    expect(pending.text()).not.toContain('审批')
  })

  it('未知 part 折叠为调试信息且不影响消息', () => {
    const wrapper = mountPart({ type: 'data-custom', data: { value: 1 } })
    expect(wrapper.get('[data-testid="ai-part-debug"]').text()).toContain('data-custom')
    expect(wrapper.text()).toContain('"value": 1')
  })
})
