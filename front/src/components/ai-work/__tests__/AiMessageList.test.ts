import { mount, type DOMWrapper } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import type { UIMessage } from 'ai'
import type { AiUiPart, PendingToolCall } from '@/types/aiWork'
import { launchDesktop } from '@/utils/desktopHandoff'
import AiMessageList from '../AiMessageList.vue'

vi.mock('@/utils/desktopHandoff', async importOriginal => ({
  ...await importOriginal<typeof import('@/utils/desktopHandoff')>(),
  launchDesktop: vi.fn(),
}))

const text = (value: string): AiUiPart => ({ type: 'text', text: value })
async function expand(details: Pick<DOMWrapper<HTMLDetailsElement>, 'element' | 'trigger'>) {
  details.element.open = true
  await details.trigger('toggle')
}
const user = (id = 'user', value = '查看采购情况'): UIMessage => ({ id, role: 'user', parts: [text(value)] })
const reply = (id: string, parts: AiUiPart[]): UIMessage => ({ id, role: 'assistant', parts })
const tool = (id: string, state = 'output-available', output: unknown = { ok: true }): AiUiPart => ({
  type: 'tool-order_purchase_query', toolCallId: id, state, input: { order_id: 'order-1' },
  ...(state === 'output-available' ? { output } : {}),
} as AiUiPart)

describe('AI 对话按轮次展示', () => {
  it('普通回答不增加空过程，推理正文不进入聊天展示', async () => {
    const wrapper = mount(AiMessageList, { props: { messages: [user(), reply('a', [text('共有两笔订单。')])] } })
    expect(wrapper.find('[data-testid="ai-turn-process"]').exists()).toBe(false)
    expect(wrapper.get('[data-testid="ai-turn-response"]').text()).toBe('共有两笔订单。')
    await wrapper.setProps({ messages: [user(), reply('a', [{ type: 'reasoning', text: '核对订单数量', state: 'done' }, text('共有两笔订单。')])] })
    expect(wrapper.find('[data-testid="ai-turn-process"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('核对订单数量')
    expect(wrapper.get('[data-testid="ai-turn-response"]').text()).toBe('共有两笔订单。')
  })

  it('实时多步骤和历史多个响应呈现同一轮，正文与附件可见、处理过程默认收起', async () => {
    const first = [text('先查询采购记录'), tool('read')]
    const last: AiUiPart[] = [text('采购单已付款。'), { type: 'file', mediaType: 'application/pdf', url: 'https://example.com/receipt.pdf', filename: '采购凭证.pdf' }]
    const live = [user(), reply('live', [{ type: 'step-start' }, ...first, { type: 'step-start' }, ...last])]
    const saved = JSON.stringify(live)
    const wrapper = mount(AiMessageList, { props: { messages: live } })
    const process = wrapper.get<HTMLDetailsElement>('[data-testid="ai-turn-process"]')
    expect(process.element.open).toBe(false)
    expect(process.find('[data-testid="ai-part-tool"]').exists()).toBe(false)
    await expand(process)
    expect(process.text()).toContain('先查询采购记录')
    expect(process.text()).toContain('查询采购记录')
    expect(wrapper.get('[data-testid="ai-turn-response"]').text()).toContain('采购单已付款。')
    expect(wrapper.get('[data-testid="ai-turn-response"] a').text()).toBe('采购凭证.pdf')
    expect(wrapper.findAll('[data-testid="ai-message-assistant"]')).toHaveLength(1)
    expect(JSON.stringify(live)).toBe(saved)

    // 历史恢复改变底层消息数量，不改变对话结构，也不重置用户已展开的过程。
    process.element.open = true
    await wrapper.setProps({ messages: [user(), reply('saved-1', first), reply('saved-2', last)] })
    expect(wrapper.findAll('[data-testid="ai-message-assistant"]')).toHaveLength(1)
    expect(wrapper.get<HTMLDetailsElement>('[data-testid="ai-turn-process"]').element.open).toBe(true)
    expect(wrapper.get('[data-testid="ai-turn-response"]').text()).toContain('采购单已付款。')
    expect(wrapper.findAll('[data-testid="ai-part-tool"]')).toHaveLength(1)
  })

  it('审批暂停和继续属于同一轮，未决定的审批始终在过程折叠区外', async () => {
    const pending: PendingToolCall[] = [{ tool_call_id: 'send', tool_name: 'order_buyer_chat_send', kind: 'approval', summary: '发送消息给买家' }]
    const wrapper = mount(AiMessageList, { props: {
      messages: [user(), reply('pending', [tool('send', 'approval-requested')])], pendingToolCalls: pending,
    } })
    expect(wrapper.get('[data-testid="ai-turn-timeline"]').text()).toContain('等待确认')
    expect(wrapper.get('[data-testid="ai-turn-timeline"]').element.closest('details')).toBeNull()

    await wrapper.setProps({ pendingToolCalls: [], messages: [user(), reply('pending', [tool('send')]), reply('resumed', [text('消息已发送。')])] })
    expect(wrapper.findAll('[data-testid="ai-message-assistant"]')).toHaveLength(1)
    expect(wrapper.find('[data-testid="ai-turn-attention"]').exists()).toBe(false)
    expect(wrapper.get('[data-testid="ai-turn-response"]').text()).toBe('消息已发送。')
  })

  it('用户中途补充不被合并隐藏，只有当前轮显示实时进度', () => {
    const wrapper = mount(AiMessageList, { props: { active: true, messages: [
      user(), reply('first', [tool('first'), text('已找到采购单。')]),
      user('correction', '先不要付款'), reply('second', [text('收到，先核对状态'), tool('second', 'input-available')]),
    ] } })
    expect(wrapper.findAll('[data-testid="ai-message-user"]')).toHaveLength(2)
    const turns = wrapper.findAll('[data-testid="ai-message-assistant"]')
    expect(turns).toHaveLength(2)
    expect(turns[0].find('[data-testid="ai-turn-progress"]').exists()).toBe(false)
    expect(turns[1].get('[data-testid="ai-turn-timeline"]').text()).toContain('正在查询采购记录')
    expect(turns[1].get('[data-testid="ai-turn-status"]').text()).toBe('正在执行…')
    expect(turns[1].find('[data-testid="ai-running-deer"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('先不要付款')
  })

  it.each([
    [{ ok: false, error: { message: '平台拒绝请求' } }, '操作失败', '平台拒绝请求'],
    [{ ok: false, error: { message: '请核对实际支付状态', details: { outcome_unknown: true } } }, '结果未确认', '请核对实际支付状态'],
    [{ status: 'partial' }, '部分完成', ''],
  ])('业务异常不藏入已折叠的过程：%j', async (output, label, message) => {
    const wrapper = mount(AiMessageList, { props: { messages: [user(), reply('a', [tool('ok'), tool('problem', 'output-available', output), text('请处理异常。')])] } })
    const attention = wrapper.get('[data-testid="ai-turn-attention"]')
    expect(attention.element.closest('details')).toBeNull()
    expect(attention.text()).toContain(label)
    expect(attention.text()).toContain(message)
    expect(attention.text()).not.toContain('已完成')
    await expand(wrapper.get<HTMLDetailsElement>('[data-testid="ai-turn-process"]'))
    expect(wrapper.get('[data-testid="ai-turn-process"] [data-testid="ai-part-tool"]').attributes('data-tool-status')).toBe('completed')
  })

  it('后台等待即使没有活动流仍显示进度；历史未返回、拒绝、错误均保持可见', async () => {
    const wrapper = mount(AiMessageList, { props: {
      messages: [user(), reply('a', [tool('job', 'approval-requested')])],
      pendingToolCalls: [{ tool_call_id: 'job', tool_name: 'order_purchase_query', kind: 'external', summary: '' }],
    } })
    expect(wrapper.get('[data-testid="ai-turn-timeline"] [data-testid="ai-part-tool"]').attributes('data-tool-status')).toBe('running')
    expect(wrapper.get('[data-testid="ai-turn-status"]').text()).toBe('正在执行…')
    expect(wrapper.find('[data-testid="ai-turn-process"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('等待确认')
    await wrapper.setProps({ pendingToolCalls: [] })
    expect(wrapper.get('[data-testid="ai-turn-attention"]').text()).toContain('结果未确认')
    expect(wrapper.find('[data-testid="ai-turn-progress"]').exists()).toBe(false)
    for (const [state, label] of [['output-denied', '未获批准'], ['output-error', '操作失败']]) {
      await wrapper.setProps({ messages: [user(), reply('a', [tool('job', state)])] })
      expect(wrapper.get('[data-testid="ai-turn-attention"]').text()).toContain(label)
    }
  })

  it('脚本检查重试收进过程，后续真实业务异常保留提示，历史刷新不改写原始错误', async () => {
    const check: AiUiPart = {
      type: 'tool-run_code', toolCallId: 'check', state: 'output-error', input: {},
      errorText: 'Type error in code:\nerror[unresolved-reference]: Name `online_products_change` used when not defined\nFix the errors and try again.',
    }
    const rejected: AiUiPart = {
      type: 'tool-online_products_change', toolCallId: 'write', state: 'output-available',
      input: {}, output: { ok: false, error: { message: '当前商品有待核对的修改' } },
    }
    const parts = [check, tool('read'), rejected, text('仍需核对原操作结果。')]
    const saved = JSON.stringify(parts)
    const wrapper = mount(AiMessageList, { props: { active: true, messages: [user(), reply('live', parts)] } })
    const timeline = wrapper.get('[data-testid="ai-turn-timeline"]')
    const checkDetails = timeline.get<HTMLDetailsElement>('[data-testid="ai-tool-details"]')
    expect(checkDetails.element.open).toBe(false)
    expect(checkDetails.get('summary').text()).toContain('重新调整')
    expect(timeline.findAll('[role="alert"]').map(alert => alert.text())).toEqual(['当前商品有待核对的修改'])

    await wrapper.setProps({ active: false })
    expect(wrapper.get<HTMLDetailsElement>('[data-testid="ai-turn-process"]').element.open).toBe(false)
    await expand(wrapper.get<HTMLDetailsElement>('[data-testid="ai-turn-process"]'))
    await expand(wrapper.get<HTMLDetailsElement>('[data-testid="ai-turn-process"] [data-testid="ai-tool-details"]'))
    expect(wrapper.get('[data-testid="ai-turn-process"]').text()).toContain('unresolved-reference')
    expect(wrapper.get('[data-testid="ai-turn-attention"]').text()).not.toContain('重新调整')
    expect(wrapper.get('[data-testid="ai-turn-attention"] [role="alert"]').text()).toBe('当前商品有待核对的修改')

    await wrapper.setProps({ messages: [user(), reply('saved-check', [check]), reply('saved-result', parts.slice(1))] })
    expect(wrapper.get('[data-testid="ai-turn-process"]').text()).toContain('重新调整')
    expect(wrapper.get('[data-testid="ai-turn-attention"]').text()).toContain('当前商品有待核对的修改')
    expect(JSON.stringify(parts)).toBe(saved)
  })

  it('脚本检查失败后正常完成的回复不残留业务失败提示', async () => {
    const wrapper = mount(AiMessageList, { props: { messages: [user(), reply('a', [
      { type: 'tool-run_code', toolCallId: 'check', state: 'output-error', input: {}, errorText: 'Syntax error in code:\ninvalid syntax' },
      tool('corrected'), text('查询完成。'),
    ])] } })
    expect(wrapper.find('[data-testid="ai-turn-attention"]').exists()).toBe(false)
    expect(wrapper.get('[data-testid="ai-turn-response"]').text()).toBe('查询完成。')
    await expand(wrapper.get<HTMLDetailsElement>('[data-testid="ai-turn-process"]'))
    expect(wrapper.get('[data-testid="ai-turn-process"]').text()).toContain('重新调整')
  })

  it('运行时诊断在实时和历史中均默认折叠，失败提示继续保留', async () => {
    const errorText = 'Runtime error:\nTraceback (most recent call last):\nNameError: Unknown function: draft_package_estimates_preview'
    const failed: AiUiPart = { type: 'tool-run_code', toolCallId: 'runtime', state: 'output-error', input: {}, errorText }
    const parts = [failed, text('请核对处理结果。')]
    const saved = JSON.stringify(parts)
    const wrapper = mount(AiMessageList, { props: { active: true, messages: [user(), reply('live', parts)] } })
    expect(wrapper.text()).toContain('操作失败')
    expect(wrapper.text()).not.toContain('Traceback')
    await wrapper.setProps({ active: false, messages: [user(), reply('history', parts)] })
    const attention = wrapper.get('[data-testid="ai-turn-attention"]')
    expect(attention.text()).toContain('操作失败')
    expect(attention.text()).not.toContain('Traceback')
    await expand(attention.get<HTMLDetailsElement>('[data-testid="ai-tool-details"]'))
    expect(attention.get('[data-testid="ai-script-diagnostic"]').text()).toBe(errorText)
    expect(JSON.stringify(parts)).toBe(saved)
  })

  it('执行中的文字和并行工具按顺序原地更新，旁白降权且完成后自动收起为语义摘要', async () => {
    const wrapper = mount(AiMessageList, { props: { active: true, messages: [user(), reply('a', [text('先查询')])] } })
    const timeline = () => wrapper.get('[data-testid="ai-turn-timeline"]')
    const prose = timeline().get('[data-testid="ai-part-text"]').element
    await wrapper.setProps({ messages: [user(), reply('a', [text('先查询'), tool('read', 'input-streaming'), tool('second', 'input-available')])] })
    expect(timeline().get('[data-testid="ai-part-text"]').element).toBe(prose)
    expect(timeline().get('[data-testid="ai-part-text"]').classes()).toContain('text-xs')
    expect(prose.closest('details')).toBeNull()
    expect(wrapper.find('[data-testid="ai-turn-process"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="ai-turn-progress"]').exists()).toBe(true)
    const rows = timeline().findAll('[data-testid="ai-part-tool"]')
    expect(rows[0].attributes('data-tool-status')).toBe('preparing')
    expect(rows[0].get('summary').text()).toContain('正在查询采购记录')
    expect(rows[1].attributes('data-tool-status')).toBe('waiting')
    expect(rows[1].get('summary').text()).toContain('正在查询采购记录')
    const detail = rows[0].get<HTMLDetailsElement>('details').element
    expect(detail.open).toBe(false)
    detail.open = true

    // step 边界和历史消息数量改变，不重建已展开的活动行；工具后的正文保持主视觉权重。
    await wrapper.setProps({ messages: [user(), reply('saved-1', [{ type: 'step-start' }, text('先查询'), tool('read')]), reply('saved-2', [{ type: 'step-start' }, tool('second', 'input-available'), text('已找到第一笔')])] })
    expect(timeline().get('[data-testid="ai-part-text"]').element).toBe(prose)
    expect(timeline().get('[data-testid="ai-part-tool"]').element).toBe(rows[0].element)
    expect(detail.open).toBe(true)
    expect(rows[0].attributes('data-tool-status')).toBe('completed')
    expect(rows[0].get('summary').text()).not.toContain('已完成')
    const texts = timeline().findAll('[data-testid="ai-part-text"]')
    expect(texts[0].classes()).toContain('text-xs')
    expect(texts[1].classes()).toContain('text-sm')
    expect(timeline().findAll('[data-testid="ai-part-text"], [data-testid="ai-part-tool"]').map(row => row.attributes('data-testid'))).toEqual(['ai-part-text', 'ai-part-tool', 'ai-part-tool', 'ai-part-text'])

    await wrapper.setProps({ active: false, messages: [user(), reply('a', [text('先查询'), tool('read'), tool('second'), text('找到了两笔订单。')])] })
    expect(wrapper.find('[data-testid="ai-turn-timeline"]').exists()).toBe(false)
    expect(wrapper.get<HTMLDetailsElement>('[data-testid="ai-turn-process"]').element.open).toBe(false)
    expect(wrapper.get('[data-testid="ai-turn-process"] summary').text()).toContain('已处理 2 步 · 查询采购记录')
    expect(wrapper.get('[data-testid="ai-turn-response"]').text()).toBe('找到了两笔订单。')
    expect(wrapper.find('[data-testid="ai-turn-progress"]').exists()).toBe(false)
  })

  it('流式正文与隐藏思考均由唯一底部小鹿统一反馈阶段，且不暴露推理正文', async () => {
    const wrapper = mount(AiMessageList, { props: { active: true, messages: [user(), reply('a', [{ type: 'text', text: '正在核对', state: 'streaming' }])] } })
    expect(wrapper.get('[data-testid="ai-turn-status"]').text()).toBe('正在回复…')
    await wrapper.setProps({ messages: [user(), reply('a', [{ type: 'reasoning', text: '核对', state: 'streaming' }])] })
    expect(wrapper.get('[data-testid="ai-turn-status"]').text()).toBe('正在思考…')
    expect(wrapper.text()).not.toContain('核对')
    await wrapper.setProps({ messages: [user(), reply('a', [tool('read')])] })
    expect(wrapper.get('[data-testid="ai-turn-status"]').text()).toBe('正在思考…')
  })

  it('运行时计时、结束时冻结用时并显示结束时间，重新打开不重新计时', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-10-13T08:00:55Z'))
    const timing = { started_at: '2026-10-13T08:00:00Z', finished_at: '', status: 'claimed' }
    const messages = [user(), { ...reply('a', [tool('read'), text('结果')]), metadata: { ai_turn: timing } }]
    const snapshot = JSON.stringify(messages)
    const wrapper = mount(AiMessageList, { props: { messages, active: true } })
    try {
      expect(wrapper.get('[data-testid="ai-turn-timing"]').text()).toContain('55秒')
      await vi.advanceTimersByTimeAsync(23000)
      expect(wrapper.get('[data-testid="ai-turn-timing"]').text()).toContain('1分钟 18秒')
      const completed = [user(), { ...reply('a', [tool('read'), text('结果')]), metadata: { ai_turn: {
        ...timing, finished_at: '2026-10-13T08:01:55Z', status: 'completed',
      } } }]
      await wrapper.setProps({ active: false, messages: completed })
      expect(wrapper.get('[data-testid="ai-turn-process"] summary').text()).toBe('已处理 1 步 · 查询采购记录 · 1分钟 55秒')
      expect(wrapper.get('time').attributes('datetime')).toBe('2026-10-13T08:01:55Z')
      await vi.advanceTimersByTimeAsync(60000)
      expect(wrapper.get('[data-testid="ai-turn-process"] summary').text()).toBe('已处理 1 步 · 查询采购记录 · 1分钟 55秒')
      const reopened = mount(AiMessageList, { props: { messages: completed } })
      expect(reopened.get('[data-testid="ai-turn-process"] summary').text()).toBe('已处理 1 步 · 查询采购记录 · 1分钟 55秒')
      reopened.unmount()
      expect(JSON.stringify(messages)).toBe(snapshot)
    } finally { wrapper.unmount(); vi.useRealTimers() }
  })

  it('复制只包含回答，分支定位到合并轮次的最后一条回复', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal('navigator', { clipboard: { writeText } })
    const wrapper = mount(AiMessageList, { props: { allowBranch: true, messages: [
      user(), reply('first', [text('先查询'), tool('read')]),
      { ...reply('last', [text('结果正文')]), metadata: { ai_turn: {
        started_at: '2026-10-13T08:00:00Z', finished_at: '2026-10-13T08:00:05Z', status: 'completed',
      } } },
    ] } })
    try {
      await wrapper.get('button[aria-label="复制这个回复"]').trigger('click')
      expect(writeText).toHaveBeenCalledWith('结果正文')
      expect(wrapper.find('button[aria-label="已复制"]').exists()).toBe(true)
      await wrapper.get('button[aria-label="分支到新的聊天"]').trigger('click')
      expect(wrapper.emitted('branch')).toEqual([['last']])
      await wrapper.setProps({ active: true })
      expect(wrapper.find('[data-testid="ai-turn-actions"]').exists()).toBe(false)
    } finally { wrapper.unmount(); vi.unstubAllGlobals() }
  })

  it('旧记录不补造用时或结束时间，剪贴板不可用时明确提示失败', async () => {
    vi.stubGlobal('navigator', {})
    const wrapper = mount(AiMessageList, { props: { messages: [user(), reply('a', [text('结果')])] } })
    try {
      expect(wrapper.find('time').exists()).toBe(false)
      expect(wrapper.find('[data-testid="ai-turn-timing"]').exists()).toBe(false)
      await wrapper.get('button[aria-label="复制这个回复"]').trigger('click')
      expect(wrapper.get('[role="alert"]').text()).toContain('复制失败')
      expect(wrapper.find('button[aria-label="已复制"]').exists()).toBe(false)
    } finally { wrapper.unmount(); vi.unstubAllGlobals() }
  })

  it('中断后未返回的调用转为结果未确认，提交后台任务不显示完成', async () => {
    const wrapper = mount(AiMessageList, { props: { active: true, messages: [user(), reply('a', [tool('pay', 'input-available')])] } })
    expect(wrapper.find('[data-testid="ai-turn-attention"]').exists()).toBe(false)
    await wrapper.setProps({ active: false })
    expect(wrapper.get('[data-testid="ai-turn-attention"]').text()).toContain('结果未确认')
    await wrapper.setProps({ messages: [user(), reply('a', [tool('publish', 'output-available', { status: 'queued' })])] })
    await expand(wrapper.get<HTMLDetailsElement>('[data-testid="ai-turn-process"]'))
    expect(wrapper.get('[data-testid="ai-turn-process"]').text()).toContain('已提交')
    expect(wrapper.text()).not.toContain('已完成')
  })

  it('历史 run_code 嵌套的桌面图片交接回执展示在关注区并去重，可点击在桌面继续', async () => {
    vi.mocked(launchDesktop).mockClear()
    const conversationId = 'conversation_global_chat_' + '5'.repeat(32)
    const makeReceipt = (toolCallId: string, draftId: string) => ({
      tool_call_id: toolCallId,
      tool_name: 'draft_images_prepare',
      output: {
        ok: false,
        error: {
          code: 'IMAGE_DESKTOP_REQUIRED',
          message: '图片翻译/重绘使用本机模型「Gemini Canvas 修图」。当前操作需要本机执行器，请从桌面端发起。',
          details: {
            desktop_image_action: { conversation_id: conversationId, tool_call_id: toolCallId },
            request: { draft_id: draftId, decision: 'auto', handoff_fingerprint: `fp-${draftId}` },
          },
        },
      },
    })
    const messages: UIMessage[] = [
      user('u1', '准备所选草稿'),
      {
        ...reply('r1', [{ type: 'tool-run_code', toolCallId: 'script-1', state: 'output-available', input: {}, output: {} } as AiUiPart]),
        metadata: { business_write_receipts: [makeReceipt('script-1__1', 'draft-a'), makeReceipt('script-1__2', 'draft-b')] },
      },
      {
        ...reply('r2', [{ type: 'tool-run_code', toolCallId: 'script-2', state: 'output-available', input: {}, output: {} } as AiUiPart, text('请在图片操作提示处点击“在桌面继续”。')]),
        metadata: { business_write_receipts: [makeReceipt('script-2__1', 'draft-a')] },
      },
    ]
    const saved = JSON.stringify(messages)
    const wrapper = mount(AiMessageList, { props: { messages } })
    const attention = wrapper.get('[data-testid="ai-turn-attention"]')
    expect(attention.element.closest('details')).toBeNull()
    expect(attention.text()).toContain('准备草稿套图 · 草稿 draft-a')
    expect(attention.text()).toContain('准备草稿套图 · 草稿 draft-b')
    expect(attention.text()).toContain('等待桌面')
    const buttons = attention.findAll('button')
    expect(buttons).toHaveLength(2)
    await buttons[0].trigger('click')
    expect(launchDesktop).toHaveBeenCalledWith({
      target: '/aiWork',
      state: { imageOperation: { conversation_id: conversationId, tool_call_id: 'script-2__1' } },
    })
    expect(JSON.stringify(messages)).toBe(saved)
  })

  it('长任务运行中自动收纳早期已完成步骤，并透出当前 run_code 子进度', async () => {
    const runStep = (id: string, code: string, state = 'output-available'): AiUiPart => ({
      type: 'tool-run_code',
      toolCallId: id,
      state,
      input: { code },
      ...(state === 'output-available' ? { output: { ok: true } } : {}),
    } as AiUiPart)
    const parts = [
      runStep('s1', 'await draft_read(draft_id="d1")'),
      runStep('s2', 'await category_match(draft_id="d1")'),
      runStep('s3', 'await copy_generate_batch(product_ids=["p1"])'),
      runStep('s4', 'await category_attributes_query(category_id="1")'),
      runStep('s5', 'await draft_attributes_read(draft_id="d1")'),
      runStep('s6', 'await draft_changes_apply(draft_id="d1")', 'input-available'),
    ]
    const wrapper = mount(AiMessageList, {
      props: {
        active: true,
        messages: [user('u1', '批量准备草稿'), reply('r1', parts)],
        codeProgress: {
          s6: {
            tool_call_id: 's6',
            tool_name: 'draft_changes_apply',
            started: 18,
            completed: 7,
            failed: 0,
            active: 11,
            tools: [{ name: 'draft_changes_apply', started: 18, completed: 7, failed: 0, active: 11 }],
          },
        },
      },
    })

    expect(wrapper.get('[data-testid="ai-turn-step-count"]').text()).toBe('6/6')
    const earlier = wrapper.get<HTMLDetailsElement>('[data-testid="ai-turn-earlier-steps"]')
    expect(earlier.element.open).toBe(false)
    expect(earlier.get('summary').text()).toContain('已完成 3 个前置步骤')
    expect(wrapper.findAll('[data-testid="ai-part-tool"]')).toHaveLength(3)
    expect(wrapper.get('[data-testid="ai-tool-progress"]').text()).toContain('7/18')
    expect(wrapper.get('[data-testid="ai-turn-status"]').text()).toBe('正在执行…')

    await expand(earlier)
    expect(wrapper.findAll('[data-testid="ai-part-tool"]')).toHaveLength(6)
    expect(earlier.text()).toContain('查看草稿')
    expect(earlier.text()).not.toContain('批量处理 ·')
  })

  it('忽略 system 内部消息且不打断同一轮助手合并，批量类目工具展示真实业务名', () => {
    const sys = {
      id: 'sys-1',
      role: 'system',
      parts: [text('New functions are now available inside `run_code`: `category_match_batch`.')],
    } as unknown as UIMessage
    const batchCall: AiUiPart = {
      type: 'tool-run_code',
      toolCallId: 'cat-batch',
      state: 'output-available',
      input: { code: 'await category_match_batch(draft_ids=["d1", "d2"])' },
      output: { ok: true },
    } as AiUiPart
    const wrapper = mount(AiMessageList, {
      props: {
        messages: [user('u1', '批量处理草稿'), reply('a1', [batchCall]), sys, reply('a2', [text('已完成批量匹配。')])],
      },
    })
    expect(wrapper.findAll('[data-testid="ai-message-assistant"]')).toHaveLength(1)
    expect(wrapper.text()).not.toContain('New functions are now available')
    expect(wrapper.get('[data-testid="ai-turn-process"] summary').text()).toContain('批量匹配商品类目')
    expect(wrapper.get('[data-testid="ai-turn-response"]').text()).toBe('已完成批量匹配。')
  })

  it('长任务含过程旁白时按完整阶段折叠，不把说明和对应工具拆开', async () => {
    const runStep = (id: string, code: string, state = 'output-available'): AiUiPart => ({
      type: 'tool-run_code',
      toolCallId: id,
      state,
      input: { code },
      ...(state === 'output-available' ? { output: { ok: true } } : {}),
    } as AiUiPart)
    const parts: AiUiPart[] = [
      text('第一阶段：先查草稿'),
      runStep('s1', 'await draft_read(draft_id="d1")'),
      text('第二阶段：核对类目'),
      runStep('s2', 'await category_match(draft_id="d1")'),
      text('第三阶段：读取属性'),
      runStep('s3', 'await draft_attributes_read(draft_id="d1")'),
      text('第四阶段：批量保存'),
      runStep('s4', 'await draft_changes_apply(draft_id="d1")', 'input-available'),
    ]
    const wrapper = mount(AiMessageList, {
      props: { active: true, messages: [user('u1', '批量处理'), reply('r1', parts)] },
    })
    const earlier = wrapper.get<HTMLDetailsElement>('[data-testid="ai-turn-earlier-steps"]')
    expect(earlier.element.open).toBe(false)
    // 前两个完整阶段（含各自旁白与工具）被收起，第三、四阶段完整留在当前视图。
    expect(wrapper.text()).not.toContain('第一阶段：先查草稿')
    expect(wrapper.text()).not.toContain('第二阶段：核对类目')
    expect(wrapper.text()).toContain('第三阶段：读取属性')
    expect(wrapper.text()).toContain('第四阶段：批量保存')
    await expand(earlier)
    expect(earlier.text()).toContain('第一阶段：先查草稿')
    expect(earlier.text()).toContain('第二阶段：核对类目')
  })

  it('run_code 嵌套的 402/401 致命模型故障按模型去重提取到关注区红色告警卡，不被绿色完成态掩盖', async () => {
    const push = vi.fn().mockResolvedValue(undefined)
    const { routerKey } = await import('vue-router')
    const make402 = (callId: string) => ({
      tool_call_id: callId,
      tool_name: 'category_match',
      output: {
        ok: false,
        error: {
          code: 'AI_PROVIDER_PAYMENT_REQUIRED',
          message: '「商品类目候选匹配」绑定的模型「deepSeekv4.1flash」余额不足或计费不可用（HTTP 402），请充值或在「授权配置 → AI 模型」切换绑定。',
          details: {
            status_code: 402,
            use_case_id: 'category.product_match',
            use_case_label: '商品类目候选匹配',
            model_id: 'ai_model_ds41',
            model_name: 'deepSeekv4.1flash',
          },
        },
      },
    })
    const make401 = (callId: string) => ({
      tool_call_id: callId,
      tool_name: 'copy_generate',
      output: {
        ok: false,
        error: {
          code: 'HTTP_401',
          message: '「商品 AI 文案」绑定的模型「badKeyModel」鉴权失败或无访问权限（HTTP 401），请检查 API Key 或在「授权配置 → AI 模型」切换绑定。',
          details: {
            status_code: 401,
            use_case_id: 'copy.generate',
            use_case_label: '商品 AI 文案',
            model_id: 'ai_model_bad',
            model_name: 'badKeyModel',
          },
        },
      },
    })
    const messages: UIMessage[] = [
      user('u1', '批量准备所选草稿'),
      {
        ...reply('r1', [
          { type: 'tool-run_code', toolCallId: 'script-1', state: 'output-available', input: { code: 'await category_match()' }, output: { ok: true } } as AiUiPart,
          text('已汇总处理情况。'),
        ]),
        metadata: {
          business_write_receipts: [
            make402('script-1__1'),
            make402('script-1__2'),
            make402('script-1__3'),
            make401('script-1__4'),
          ],
        },
      },
    ]
    const saved = JSON.stringify(messages)
    const wrapper = mount(AiMessageList, {
      global: { provide: { [routerKey as symbol]: { push } } },
      props: { messages },
    })

    // 绿色 run_code 收起在过程区，而去重后的 2 条致命模型告警直接外显在关注区。
    expect(wrapper.get<HTMLDetailsElement>('[data-testid="ai-turn-process"]').element.open).toBe(false)
    const attention = wrapper.get('[data-testid="ai-turn-attention"]')
    expect(attention.element.closest('details')).toBeNull()
    const alerts = attention.findAll('[data-testid="ai-model-infra-alert"]')
    expect(alerts).toHaveLength(2)
    expect(alerts[0].text()).toContain('商品类目候选匹配')
    expect(alerts[0].text()).toContain('deepSeekv4.1flash')
    expect(alerts[0].get('[data-testid="ai-model-infra-alert-count"]').text()).toContain('影响 3 次调用')
    expect(alerts[1].text()).toContain('商品 AI 文案')
    expect(alerts[1].text()).toContain('badKeyModel')

    await alerts[0].get('[data-testid="ai-model-alert-open-bindings"]').trigger('click')
    expect(push).toHaveBeenCalledWith({ path: '/', query: { tab: 'auth', auth_section: 'ai_bindings' } })
    await alerts[0].get('[data-testid="ai-model-alert-open-models"]').trigger('click')
    expect(push).toHaveBeenCalledWith({ path: '/', query: { tab: 'auth', auth_section: 'ai_models' } })
    expect(JSON.stringify(messages)).toBe(saved)
  })

  it('等待用户回复提问时直接在消息区展示完整问题，且暂停顶部持续计时', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-11-26T15:16:00Z'))
    const timing = { started_at: '2026-11-26T15:01:16Z', finished_at: '', status: 'claimed' }
    const promptPart = {
      type: 'tool-request_user_input',
      toolCallId: 'ask-1',
      state: 'approval-requested',
      input: {
        title: '确认包装与库存',
        question: '1. 请提供外包装尺寸。\n2. 充气泵库存是多少？',
      },
    } as unknown as AiUiPart
    const pending: PendingToolCall[] = [{
      tool_call_id: 'ask-1',
      tool_name: 'request_user_input',
      kind: 'input',
      summary: '',
      title: '确认包装与库存',
      question: '1. 请提供外包装尺寸。\n2. 充气泵库存是多少？',
    }]
    const wrapper = mount(AiMessageList, {
      props: {
        active: false,
        pendingToolCalls: pending,
        messages: [
          user('u1', '处理草稿'),
          { ...reply('a1', [tool('read-1'), promptPart]), metadata: { ai_turn: timing } },
        ],
      },
    })
    try {
      expect(wrapper.get('[data-testid="ai-turn-status"]').text()).toBe('等待你回复')
      expect(wrapper.find('[data-testid="ai-turn-timing"]').exists()).toBe(false)
      expect(wrapper.find('[data-testid="ai-turn-step-count"]').exists()).toBe(false)
      expect(wrapper.get('[data-testid="ai-running-deer"]').attributes('data-gait')).toBe('idle')
      const card = wrapper.get('[data-testid="ai-part-user-input"]')
      expect(card.element.closest('details')).toBeNull()
      expect(card.text()).toContain('确认包装与库存')
      expect(card.text()).toContain('1. 请提供外包装尺寸。')
      expect(card.text()).toContain('等待回复')

      // 推进时间后不应变成持续累加耗时。
      await vi.advanceTimersByTimeAsync(120_000)
      expect(wrapper.get('[data-testid="ai-turn-status"]').text()).toBe('等待你回复')
      expect(wrapper.find('[data-testid="ai-turn-timing"]').exists()).toBe(false)
    } finally {
      wrapper.unmount()
      vi.useRealTimers()
    }
  })
})
