import { describe, expect, it } from 'vitest'
import { splitAssistantText, visibleAssistantText } from '../aiThinkingPresentation'

describe('思考文本的展示投影', () => {
  it('允许连续开头标签，正文中的标签原样保留', () => {
    const text = '\n<think>甲</think>\n<think>乙</think>答案举例：<think>丙</think>'
    expect(splitAssistantText(text)).toEqual([
      { kind: 'thinking', text: '甲' }, { kind: 'thinking', text: '乙' },
      { kind: 'text', text: '答案举例：<think>丙</think>' },
    ])
    expect(visibleAssistantText(text)).toBe('答案举例：<think>丙</think>')
  })

  it('不根据语言切换或 Final answer 字样猜测异常内容的分界', () => {
    const text = '<think>分析 Final answer: 中文答复'
    expect(splitAssistantText(text)).toEqual([{ kind: 'unseparated', text }])
    expect(visibleAssistantText(text)).toBe('')
  })

  it('流结束后不把普通残缺标签无限当成思考', () => {
    expect(visibleAssistantText('<thi', true)).toBe('')
    expect(visibleAssistantText('<thi')).toBe('<thi')
  })

  it('正文尾部残留 DeepSeek DSML 闭合标记时按未执行工具文本折叠', () => {
    const leaked = 'Now: collect SKU dims.\n</｜｜DSML｜｜ parameter>\n</｜｜DSML｜｜ invoke>\n</｜｜DSML｜｜ calls>'
    expect(splitAssistantText(leaked)).toEqual([{ kind: 'tool-markup', text: leaked }])
    expect(visibleAssistantText(leaked)).toBe('')
  })

  it('正文开头出现未闭合的 DeepSeek DSML calls 标记时即时按未执行工具文本折叠', () => {
    const leaked = '\n\n<｜｜DSML｜｜ calls>\n<｜｜DSML｜｜ invoke name="run_code">'
    expect(splitAssistantText(leaked, true)).toEqual([{ kind: 'tool-markup', text: leaked }])
    expect(visibleAssistantText(leaked, true)).toBe('')
  })
})
