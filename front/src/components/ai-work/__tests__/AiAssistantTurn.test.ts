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

  it('奔跑小鹿作为底部唯一活跃锚点全程常驻，随思考、工具、流式回复与停滞切换步态，完成后收起', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-11-27T08:00:00Z'))
    const wrapper = mount(AiAssistantTurn, { props: { active: true, parts: [] } })
    try {
      const progress = () => wrapper.get('[data-testid="ai-turn-progress"]')
      const deer = () => progress().get('[data-testid="ai-running-deer"]')
      expect(wrapper.findAll('[data-testid="ai-running-deer"]')).toHaveLength(1)
      expect(wrapper.get('[data-testid="ai-turn-status"]').text()).toBe('正在思考…')
      expect(deer().get('svg').attributes('viewBox')).toBe('0 0 320 256')
      expect(deer().attributes('data-gait')).toBe('trot')

      // 长时间无任何输出且无活动工具 -> 平滑降速为四拍缓步。
      await vi.advanceTimersByTimeAsync(7000)
      expect(deer().attributes('data-gait')).toBe('slow')
      expect(deer().attributes('data-pace')).toBe('0.46')

      // 隐藏思考高速输出时，小鹿加速切入飞跃疾驰。
      await vi.advanceTimersByTimeAsync(200)
      await wrapper.setProps({
        parts: [{ type: 'reasoning', text: '正在快速梳理商品类目、属性、库存与核价规则，准备生成最终答复内容。', state: 'streaming' }],
      })
      expect(deer().attributes('data-gait')).toBe('gallop')
      expect(Number(deer().attributes('data-pace'))).toBeGreaterThan(1.4)

      // 工具执行中，小鹿保持常驻并以精简阶段文案反馈，不与上方工具标题重复。
      await wrapper.setProps({
        parts: [{ type: 'tool-draft_attributes_read', toolCallId: 'c1', state: 'input-available', input: {} } as never],
      })
      expect(wrapper.find('[data-testid="ai-running-deer"]').exists()).toBe(true)
      expect(wrapper.get('[data-testid="ai-turn-status"]').text()).toBe('正在执行…')

      // 可见正文流式输出时，小鹿继续常驻并提示正在回复。
      await wrapper.setProps({
        parts: [{ type: 'text', text: '开始输出最终答复。', state: 'streaming' }],
      })
      expect(wrapper.find('[data-testid="ai-running-deer"]').exists()).toBe(true)
      expect(wrapper.get('[data-testid="ai-turn-status"]').text()).toBe('正在回复…')

      // 回合完成后退场，不残留运行小鹿。
      await wrapper.setProps({
        active: false,
        parts: [{ type: 'text', text: '处理完成。' }],
      })
      expect(wrapper.find('[data-testid="ai-turn-progress"]').exists()).toBe(false)
      expect(wrapper.find('[data-testid="ai-running-deer"]').exists()).toBe(false)
    } finally {
      wrapper.unmount()
      vi.useRealTimers()
    }
  })

  it('系统开启减少动态效果时，小鹿停在静态站姿且不启动动画帧循环', () => {
    const raf = vi.fn(() => 1)
    vi.stubGlobal('requestAnimationFrame', raf)
    vi.stubGlobal('matchMedia', vi.fn().mockReturnValue({
      matches: true,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }))
    const wrapper = mount(AiAssistantTurn, { props: { active: true, parts: [] } })
    try {
      expect(wrapper.find('[data-testid="ai-running-deer"]').exists()).toBe(true)
      expect(raf).not.toHaveBeenCalled()
    } finally {
      wrapper.unmount()
    }
  })
})
