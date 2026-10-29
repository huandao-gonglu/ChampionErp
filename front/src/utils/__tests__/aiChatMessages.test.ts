import { describe, expect, it, vi } from 'vitest'
import type { UIMessage } from 'ai'
import { projectChatMessages } from '../aiChatMessages'

describe('流式消息的浅投影', () => {
  it('392 条大回执与 4090 次空白更新保持历史引用，不遍历或复制回执内容', () => {
    const readPayload = vi.fn(() => 'x'.repeat(7000))
    const history: UIMessage[] = Array.from({ length: 392 }, (_, i) => ({
      id: `history-${i}`, role: 'assistant', parts: [{ type: 'tool-online_products_read',
        toolCallId: `call-${i}`, state: 'output-available', input: {},
        output: { get payload() { return readPayload() } } }],
    }))
    const current: UIMessage = { id: 'live', role: 'assistant', parts: [{ type: 'text', text: '我先处理。', state: 'streaming' }] }
    const source = [...history, current]
    let view = projectChatMessages(source, '')
    const initial = view
    for (let i = 0; i < 4090; i++) {
      const part = current.parts[0]!
      if (part.type === 'text') part.text += ' \n'
      // 与 SDK 相同：原地更新 part，再浅替换最后一条 message。
      source[source.length - 1] = { ...current }
      view = projectChatMessages(source, '', view)
      expect(view[0]).toBe(initial[0])
      expect(view[391]).toBe(initial[391])
    }
    expect(readPayload).not.toHaveBeenCalled()
    expect(view.at(-1)?.parts[0]).toMatchObject({ text: '我先处理。' + ' \n'.repeat(4090) })
    expect(initial.at(-1)?.parts[0]).toMatchObject({ text: '我先处理。' })
  })

  it('保留工具状态变化，计时只作用于展示，不修改 SDK 原始消息', () => {
    const source: UIMessage[] = [
      { id: 'user', role: 'user', parts: [{ type: 'text', text: '执行' }] },
      { id: 'call', role: 'assistant', parts: [{ type: 'tool-read', toolCallId: 'c', state: 'input-streaming' }] },
    ]
    const first = projectChatMessages(source, '2026-10-28T15:00:00Z')
    Object.assign(source[1]!.parts[0]!, { state: 'output-available', output: { ok: true } })
    const second = projectChatMessages(source, '2026-10-28T15:00:00Z', first)
    expect(source[0]!.metadata).toBeUndefined()
    expect(second[0]).toBe(first[0])
    expect(first[1]!.parts[0]).toMatchObject({ state: 'input-streaming' })
    expect(second[1]!.parts[0]).toMatchObject({ state: 'output-available', output: { ok: true } })
    expect(projectChatMessages([], '', second)).toEqual([])
  })
})
