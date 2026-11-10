import { mount, type DOMWrapper } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import type { AiUiPart } from '@/types/aiWork'
import AiMessagePart from '../AiMessagePart.vue'

function mountPart(part: Record<string, unknown>) {
  return mount(AiMessagePart, {
    props: { part: part as AiUiPart },
  })
}

async function expand(details: Pick<DOMWrapper<HTMLDetailsElement>, 'element' | 'trigger'>) {
  details.element.open = true
  await details.trigger('toggle')
}

describe('AiMessagePart', () => {
  it('标签完整时折叠思考，只在正文展示答复', async () => {
    const wrapper = mountPart({ type: 'text', text: '<think>内部分析</think>最终答复' })
    expect(wrapper.get('[data-testid="ai-part-text"]').text()).toBe('最终答复')
    const details = wrapper.get<HTMLDetailsElement>('[data-testid="ai-part-reasoning"]')
    expect(details.element.open).toBe(false)
    expect(details.text()).not.toContain('内部分析')
    await expand(details)
    expect(details.text()).toContain('内部分析')
  })

  it('未闭合标签整段折叠保留，包括混在其中的业务答复', async () => {
    const original = '<think>内部分析。Final response in Chinese.库存修改没有提交。'
    const wrapper = mountPart({ type: 'text', text: original, state: 'done' })
    expect(wrapper.find('[data-testid="ai-part-text"]').exists()).toBe(false)
    const details = wrapper.get<HTMLDetailsElement>('[data-testid="ai-part-unseparated"]')
    expect(details.element.open).toBe(false)
    expect(details.get('summary').text()).toBe('查看回复原文（含思考内容）')
    expect(details.text()).not.toContain(original)
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
    await expand(details)
    expect(details.text()).toContain('库存修改没有提交。')
    expect(wrapper.props('part')).toEqual({ type: 'text', text: original, state: 'done' })
  })

  it('流式标签逐字到达时不把思考片段暴露为正文', async () => {
    const wrapper = mountPart({ type: 'text', text: '', state: 'streaming' })
    const prefix = '<think>内部分析</think>'
    for (let size = 1; size <= prefix.length; size++) {
      await wrapper.setProps({ part: { type: 'text', text: prefix.slice(0, size), state: 'streaming' } as AiUiPart })
      expect(wrapper.find('[data-testid="ai-part-text"]').exists()).toBe(false)
      expect(wrapper.get<HTMLDetailsElement>('details').element.open).toBe(false)
    }
    await wrapper.setProps({ part: { type: 'text', text: prefix + '最终答复', state: 'done' } as AiUiPart })
    expect(wrapper.get('[data-testid="ai-part-text"]').text()).toBe('最终答复')
  })

  it.each(['例子：<think>原样</think>', '```xml\n<think>原样</think>\n```', '<thing>普通标签'])('普通内容保持原样：%s', text => {
    const wrapper = mountPart({ type: 'text', text })
    expect(wrapper.get('[data-testid="ai-part-text"]').text()).toBe(text)
    expect(wrapper.find('details').exists()).toBe(false)
  })

  it('渲染 text 与默认折叠的 reasoning', async () => {
    const text = mountPart({ type: 'text', text: '回答正文' })
    expect(text.get('[data-testid="ai-part-text"]').text()).toBe('回答正文')

    const reasoning = mountPart({
      type: 'reasoning',
      text: '分析过程',
      state: 'streaming',
    })
    expect(reasoning.get('[data-testid="ai-part-reasoning"] summary').text()).toBe('思考中…')
    expect(reasoning.text()).not.toContain('分析过程')
    await expand(reasoning.get<HTMLDetailsElement>('details'))
    expect(reasoning.text()).toContain('分析过程')
  })

  it('按状态渲染工具卡', async () => {
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
    expect(card.find('pre').exists()).toBe(false)
    await expand(card.get<HTMLDetailsElement>('details'))
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

  it.each(['Type', 'Syntax'])('执行前 %s 诊断保留在默认折叠的详情中', async kind => {
    const errorText = `${kind} error in code:\nmain.py:20:21\nFix the errors and try again.`
    const wrapper = mountPart({
      type: 'dynamic-tool', toolName: 'run_code', state: 'output-error', errorText,
    })
    const details = wrapper.get<HTMLDetailsElement>('[data-testid="ai-tool-details"]')
    expect(details.element.open).toBe(false)
    expect(details.get('summary').text()).toContain('脚本检查未通过')
    expect(details.find('[data-testid="ai-script-diagnostic"]').exists()).toBe(false)
    await expand(details)
    expect(details.get('[data-testid="ai-script-diagnostic"]').text()).toBe(errorText)
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('操作失败')
    expect(wrapper.text()).not.toContain('已修复')
  })

  it.each([
    ['run_code', '未知执行错误', undefined, '操作失败'],
    ['online_products_change', 'Type error in code:\n接口失败', undefined, '操作失败'],
  ])('无法识别的脚本错误及其他工具错误保持可见：%s %s', (toolName, errorText, output, label) => {
    const wrapper = mountPart({ type: `tool-${toolName}`, state: 'output-error', errorText, output })
    const alert = wrapper.get('[role="alert"]')
    expect(alert.text()).toBe(errorText)
    expect(alert.element.closest('details')).toBeNull()
    expect(wrapper.get('summary').text()).toContain(label)
    expect(wrapper.find('[data-testid="ai-script-diagnostic"]').exists()).toBe(false)
  })

  it.each([
    ['Runtime error:\nTraceback (most recent call last):\nNameError: Unknown function: draft_package_estimates_preview', undefined, '操作失败', '此次操作未成功'],
    ['Runtime error:\nType error in code:\n写入中断', undefined, '操作失败', '此次操作未成功'],
    ['Type error in code:\n诊断', { ok: false, error: { message: '平台拒绝' } }, '操作失败', '平台拒绝'],
    ['Runtime error:\n写入中断', { outcome_unknown: true }, '结果未确认', '尚未收到明确结果'],
  ])('脚本诊断默认折叠，仍保留实际失败或未知结果：%s', async (errorText, output, label, hint) => {
    const wrapper = mountPart({ type: 'tool-run_code', state: 'output-error', errorText, output })
    const details = wrapper.get<HTMLDetailsElement>('[data-testid="ai-tool-details"]')
    expect(details.element.open).toBe(false)
    expect(details.get('summary').text()).toContain(label)
    expect(wrapper.text()).toContain(hint)
    expect(wrapper.text()).not.toContain(errorText)
    await expand(details)
    expect(details.get('[data-testid="ai-script-diagnostic"]').text()).toBe(errorText)
    expect(wrapper.findAll('[role="alert"]').some(alert => alert.text().includes(errorText))).toBe(false)
  })

  it('历史导出的未返回工具不能被断言为等待审批', () => {
    const pending = mountPart({ type: 'tool-category_match', state: 'approval-requested' })
    expect(pending.text()).toContain('结果未确认')
    expect(pending.text()).not.toContain('审批')
  })

  it('未知 part 折叠为调试信息且不影响消息', async () => {
    const wrapper = mountPart({ type: 'data-custom', data: { value: 1 } })
    expect(wrapper.find('pre').exists()).toBe(false)
    await expand(wrapper.get<HTMLDetailsElement>('details'))
    expect(wrapper.get('[data-testid="ai-part-debug"]').text()).toContain('data-custom')
    expect(wrapper.text()).toContain('"value": 1')
  })

  it('折叠工具不读取大载荷，展开后显示，收起释放 DOM', async () => {
    const payload = vi.fn(() => ({ payload: '大回执'.repeat(10000) }))
    const wrapper = mountPart({ type: 'tool-read', toolCallId: 'read', state: 'output-available',
      output: { toJSON: payload } })
    expect(payload).not.toHaveBeenCalled()
    const details = wrapper.get<HTMLDetailsElement>('details')
    await expand(details)
    expect(payload).toHaveBeenCalledOnce()
    expect(details.get('pre').text()).toContain('大回执')
    details.element.open = false
    await details.trigger('toggle')
    expect(details.find('pre').exists()).toBe(false)
    wrapper.unmount()
  })

  it('4090 次尾部空白不改变正文 DOM，后续正文仍保留中间空白', async () => {
    const wrapper = mountPart({ type: 'text', text: '我先处理。', state: 'streaming' })
    const element = wrapper.get('[data-testid="ai-part-text"]').element
    const mutated = vi.fn()
    const observer = new MutationObserver(mutated)
    observer.observe(element, { childList: true, characterData: true, subtree: true })
    let text = '我先处理。'
    for (let i = 0; i < 4090; i++) {
      text += ' \n'
      await wrapper.setProps({ part: { type: 'text', text, state: 'streaming' } as AiUiPart })
    }
    expect(mutated).not.toHaveBeenCalled()
    expect(element.textContent).toBe('我先处理。')
    expect((wrapper.props('part') as { text: string }).text).toBe(text)
    await wrapper.setProps({ part: { type: 'text', text: text + '实际答复', state: 'done' } as AiUiPart })
    expect(element.textContent).toBe(text + '实际答复')
    observer.disconnect()
    wrapper.unmount()
  })
})
