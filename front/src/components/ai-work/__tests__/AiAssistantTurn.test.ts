import { mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import AiAssistantTurn from '../AiAssistantTurn.vue'

afterEach(() => vi.unstubAllGlobals())

describe('AiAssistantTurn 的正文标签展示', () => {
  it('复制回复仅包含明确的答复，不包含折叠的思考', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal('navigator', { clipboard: { writeText } })
    const wrapper = mount(AiAssistantTurn, { props: {
      parts: [{ type: 'text', text: '<think>内部分析</think>答复内容' }],
    } })
    await wrapper.get('button').trigger('click')
    expect(writeText).toHaveBeenCalledWith('答复内容')
    wrapper.unmount()
  })

  it('未分开的回复保留在折叠块，不提供误导性的正文复制', () => {
    const wrapper = mount(AiAssistantTurn, { props: {
      parts: [{ type: 'text', text: '<think>分析和答复混在一起' }],
    } })
    expect(wrapper.get<HTMLDetailsElement>('[data-testid="ai-part-unseparated"]').element.open).toBe(false)
    expect(wrapper.find('button').exists()).toBe(false)
    wrapper.unmount()
  })

  it('仅收到折叠内容时继续显示执行进度', () => {
    const wrapper = mount(AiAssistantTurn, { props: {
      active: true, parts: [{ type: 'text', text: '<think>分析中', state: 'streaming' }],
    } })
    expect(wrapper.find('[data-testid="ai-turn-progress"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="ai-part-text"]').exists()).toBe(false)
    wrapper.unmount()
  })
})
