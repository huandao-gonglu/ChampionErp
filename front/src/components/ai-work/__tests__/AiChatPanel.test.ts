import { flushPromises, mount } from '@vue/test-utils'
import type { VueWrapper } from '@vue/test-utils'
import type { UIMessage } from 'ai'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import AiChatPanel from '../AiChatPanel.vue'

vi.mock('@/stores', () => ({
  useAiChatStore: () => ({ pendingToolCalls: [], canStop: false, stopping: false }),
}))

let resizeCallback: ResizeObserverCallback
const disconnect = vi.fn()
const wrappers: VueWrapper[] = []

beforeEach(() => {
  disconnect.mockClear()
  vi.stubGlobal('ResizeObserver', class {
    constructor(callback: ResizeObserverCallback) { resizeCallback = callback }
    observe() {}
    disconnect = disconnect
  })
})

afterEach(() => {
  for (const wrapper of wrappers.splice(0)) wrapper.unmount()
  vi.unstubAllGlobals()
})

function messages(text = '正在回复'): UIMessage[] {
  return [{ id: 'reply', role: 'assistant', parts: [{ type: 'text', text }] }]
}

async function mountPanel() {
  const wrapper = mount(AiChatPanel, {
    props: { messages: messages(), busy: true, input: '', conversationId: 'first' },
    global: { stubs: { AiChatComposer: true, NativeToolApprovals: true } },
  })
  wrappers.push(wrapper)
  const scroll = wrapper.get<HTMLElement>('[data-testid="ai-chat-scroll"]')
  const size = { height: 1000, viewport: 400 }
  let scrollTop = 0
  // jsdom 不提供布局与滚动边界，模拟浏览器对 scrollTop 的截断。
  Object.defineProperties(scroll.element, {
    scrollHeight: { get: () => size.height },
    clientHeight: { get: () => size.viewport },
    scrollTop: {
      get: () => scrollTop,
      set: (value: number) => { scrollTop = Math.max(0, Math.min(value, size.height - size.viewport)) },
    },
  })
  await flushPromises()
  const jump = () => wrapper.find('[data-testid="ai-chat-jump-to-latest"]')
  const scrollTo = async (top: number) => {
    scroll.element.scrollTop = top
    await scroll.trigger('scroll')
  }
  const stream = async () => {
    size.height += 200
    await wrapper.setProps({ messages: messages(`回复长度 ${size.height}`) })
    await flushPromises()
  }
  const resize = async () => {
    resizeCallback([], {} as ResizeObserver)
    await flushPromises()
  }
  return { wrapper, scroll, size, jump, scrollTo, stream, resize }
}

describe('AiChatPanel 消息滚动', () => {
  it('首次打开与流式追加默认跟随最新内容，底部不显示箭头', async () => {
    const { scroll, stream, jump } = await mountPanel()
    expect(scroll.element.scrollTop).toBe(600)
    await stream()
    expect(scroll.element.scrollTop).toBe(800)
    expect(jump().exists()).toBe(false)
  })

  it('向上拖动查看历史后，继续输出与回复结束都保留阅读位置', async () => {
    const { wrapper, scroll, scrollTo, stream, jump } = await mountPanel()
    await scrollTo(300)
    expect(jump().attributes('aria-label')).toBe('跳转到最新消息')
    await stream()
    await stream()
    await wrapper.setProps({ busy: false, error: '回复中断' })
    await flushPromises()
    expect(scroll.element.scrollTop).toBe(300)
    expect(jump().exists()).toBe(true)
  })

  it('点击箭头返回最新消息，并恢复后续流式跟随', async () => {
    const { scroll, scrollTo, stream, jump } = await mountPanel()
    await scrollTo(300)
    await stream()
    await jump().trigger('click')
    await flushPromises()
    expect(scroll.element.scrollTop).toBe(800)
    expect(jump().exists()).toBe(false)
    await stream()
    expect(scroll.element.scrollTop).toBe(1000)
  })

  it('向下浏览历史时仍暂停，手动滚到底部才恢复跟随', async () => {
    const { scroll, scrollTo, stream, jump } = await mountPanel()
    await scrollTo(200)
    await scrollTo(450)
    await stream()
    expect(scroll.element.scrollTop).toBe(450)
    expect(jump().exists()).toBe(true)
    await scrollTo(800)
    expect(jump().exists()).toBe(false)
    await stream()
    expect(scroll.element.scrollTop).toBe(1000)
  })

  it.each(['wheel', 'touch', 'keyboard'])('%s 向上浏览意图会阻止同一帧已排队的自动滚动', async (input) => {
    const { wrapper, scroll, size, jump } = await mountPanel()
    size.height += 200
    const update = wrapper.setProps({ messages: messages('继续输出') })
    if (input === 'wheel') {
      scroll.element.dispatchEvent(new WheelEvent('wheel', { deltaY: -100 }))
    } else if (input === 'touch') {
      void scroll.trigger('touchstart', { touches: [{ clientY: 100 }] })
      scroll.element.dispatchEvent(new TouchEvent('touchmove', { touches: [{ clientY: 200 } as Touch] }))
    } else {
      scroll.element.dispatchEvent(new KeyboardEvent('keydown', { key: 'PageUp' }))
    }
    await update
    await flushPromises()
    expect(scroll.element.scrollTop).toBe(600)
    expect(jump().exists()).toBe(true)
  })

  it('内容与可视区域尺寸变化只在跟随状态下滚动，布局触发的 scroll 不会误暂停', async () => {
    const { scroll, size, resize, stream, scrollTo } = await mountPanel()
    size.height += 200
    // 浏览器可能因布局变化发送 scroll，即便 scrollTop 没有变化。
    await scroll.trigger('scroll')
    await resize()
    expect(scroll.element.scrollTop).toBe(800)
    size.viewport = 300
    await resize()
    expect(scroll.element.scrollTop).toBe(900)
    await scrollTo(250)
    size.height += 200
    await resize()
    await stream()
    expect(scroll.element.scrollTop).toBe(250)
  })

  it('切换会话恢复默认跟随，卸载时断开尺寸观察', async () => {
    const { wrapper, scroll, scrollTo, jump, stream } = await mountPanel()
    await scrollTo(200)
    await wrapper.setProps({ conversationId: 'second', messages: messages('另一会话') })
    await flushPromises()
    expect(scroll.element.scrollTop).toBe(600)
    expect(jump().exists()).toBe(false)
    await stream()
    expect(scroll.element.scrollTop).toBe(800)
    wrapper.unmount()
    expect(disconnect).toHaveBeenCalledOnce()
    wrappers.splice(wrappers.indexOf(wrapper), 1)
  })
})
