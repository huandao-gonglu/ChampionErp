import { createPinia, setActivePinia } from 'pinia'
import { mount } from '@vue/test-utils'
import type { VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import AiChatComposer from '../AiChatComposer.vue'
import { useAiChatStore } from '@/stores/aiChat'
import { useAiPageContextStore } from '@/stores/aiPageContext'

const mountedComposers: VueWrapper[] = []

afterEach(() => {
  for (const wrapper of mountedComposers.splice(0)) wrapper.unmount()
  vi.restoreAllMocks()
})

function mountComposer(initialInput = '') {
  const pinia = createPinia()
  setActivePinia(pinia)
  const store = useAiChatStore()
  store.input = initialInput
  const wrapper = mount(AiChatComposer, {
    attachTo: document.body,
    global: {
      plugins: [pinia],
    },
    props: {
      modelValue: initialInput,
      busy: false,
      sendDisabledReason: '',
      // 模拟父组件 `@update:input="chatStore.input = $event"` 的双向绑定。
      'onUpdate:modelValue': (value: string) => {
        store.input = value
        void wrapper.setProps({ modelValue: value })
      },
    },
  })
  mountedComposers.push(wrapper)
  return { store, wrapper }
}

async function typeInput(wrapper: VueWrapper, value: string) {
  await wrapper.get('[data-testid="ai-chat-input"]').setValue(value)
}

describe('AiChatComposer 斜杠命令面板', () => {
  afterEach(() => vi.restoreAllMocks())
  beforeEach(() => {
    // pinia 在每个用例内由 mountComposer 重建。
    localStorage.removeItem('ai-chat-read-background')
  })

  it('眼睛开关显示背景提示、切换为斜杠眼睛并记住选择，不发送消息', async () => {
    const saved = new Map<string, string>()
    vi.spyOn(localStorage, 'getItem').mockImplementation(key => saved.get(key) ?? null)
    vi.spyOn(localStorage, 'setItem').mockImplementation((key, value) => { saved.set(key, value) })
    const { wrapper } = mountComposer('填写属性')
    const background = useAiPageContextStore()
    background.setSource(Symbol(), { page: 'draft_editor', section: 'category', draft_id: 'draft-a' })
    const button = wrapper.get('[data-testid="ai-chat-background-toggle"]')
    expect(button.attributes('aria-pressed')).toBe('true')
    await wrapper.vm.$nextTick()
    expect(button.attributes('title')).toContain('当前草稿 ID：draft-a')
    expect(wrapper.findComponent({ name: 'PhEye' }).exists()).toBe(true)
    await button.trigger('click')
    expect(button.attributes('aria-pressed')).toBe('false')
    expect(wrapper.findComponent({ name: 'PhEyeSlash' }).exists()).toBe(true)
    expect(localStorage.getItem('ai-chat-read-background')).toBe('off')
    expect(wrapper.emitted('send')).toBeUndefined()
    setActivePinia(createPinia())
    expect(useAiPageContextStore().enabled).toBe(false)
    wrapper.unmount()
  })

  it('按 / 之后的前缀过滤命令，无匹配时关闭面板', async () => {
    const { wrapper } = mountComposer()
    await typeInput(wrapper, '/ne')
    expect(wrapper.find('[data-testid="ai-chat-command-new"]').exists()).toBe(true)

    await typeInput(wrapper, '/x')
    expect(wrapper.find('[data-testid="ai-chat-command-panel"]').exists()).toBe(false)
  })

  it('非 / 开头的输入不触发面板', async () => {
    const { wrapper } = mountComposer()
    await typeInput(wrapper, '查询草稿')
    expect(wrapper.find('[data-testid="ai-chat-command-panel"]').exists()).toBe(false)
  })

  it('Enter 选中高亮命令执行：/new 开始新会话并清空输入', async () => {
    const { store, wrapper } = mountComposer()
    await typeInput(wrapper, '/')
    expect(store.activeConversationId).toBeNull()

    await wrapper.get('[data-testid="ai-chat-input"]').trigger('keydown', { key: 'Enter' })

    expect(store.activeConversationId).toMatch(/^conversation_global_chat_[0-9a-f]{32}$/)
    expect(store.input).toBe('')
    await wrapper.setProps({ modelValue: store.input })
    expect(wrapper.find('[data-testid="ai-chat-command-panel"]').exists()).toBe(false)
  })

  it('Escape 关闭面板并保留输入', async () => {
    const { store, wrapper } = mountComposer()
    await typeInput(wrapper, '/')
    expect(wrapper.find('[data-testid="ai-chat-command-panel"]').exists()).toBe(true)

    await wrapper.get('[data-testid="ai-chat-input"]').trigger('keydown', { key: 'Escape' })

    expect(wrapper.find('[data-testid="ai-chat-command-panel"]').exists()).toBe(false)
    expect(store.input).toBe('/')
  })

  it('busy 时不显示命令面板', async () => {
    const { wrapper } = mountComposer('/')
    await wrapper.setProps({ busy: true })
    expect(wrapper.find('[data-testid="ai-chat-command-panel"]').exists()).toBe(false)
  })
})

it('运行中输入框和发送按钮仍可提交，停止按钮单独触发停止', async () => {
  const { wrapper } = mountComposer('补充资料')
  await wrapper.setProps({ busy: true })
  expect(wrapper.get('[data-testid="ai-chat-input"]').attributes('disabled')).toBeUndefined()
  expect(wrapper.get('[data-testid="ai-chat-send"]').isVisible()).toBe(true)
  expect(wrapper.get('[data-testid="ai-chat-send"]').attributes('disabled')).toBeUndefined()
  expect(wrapper.get('[data-testid="ai-chat-stop"]').text()).toBe('停止')
  await wrapper.get('[data-testid="ai-chat-composer"]').trigger('submit')
  expect(wrapper.emitted('send')).toHaveLength(1)
  await wrapper.get('[data-testid="ai-chat-stop"]').trigger('click')
  expect(wrapper.emitted('stop')).toHaveLength(1)
})

describe('双击 Esc 停止', () => {
  it('第一次显示 Esc，第二次触发停止，长按重复事件不算第二次', async () => {
    const { wrapper } = mountComposer('补充资料')
    await wrapper.setProps({ busy: true })
    const input = wrapper.get('[data-testid="ai-chat-input"]')
    const stop = wrapper.get('[data-testid="ai-chat-stop"]')
    await input.trigger('keydown', { key: 'Escape' })
    expect(stop.text()).toBe('Esc')
    expect(stop.attributes('aria-label')).toBe('再次按 Esc 停止当前操作')
    expect(wrapper.emitted('stop')).toBeUndefined()
    await input.trigger('keydown', { key: 'Escape', repeat: true })
    expect(wrapper.emitted('stop')).toBeUndefined()
    await input.trigger('keydown', { key: 'Escape' })
    expect(wrapper.emitted('stop')).toHaveLength(1)
    expect(stop.text()).toBe('停止')
    expect(wrapper.emitted('send')).toBeUndefined()
  })

  it('焦点切到另一个控件再回来时，需要重新按两次', async () => {
    const { wrapper } = mountComposer()
    await wrapper.setProps({ busy: true })
    const input = wrapper.get<HTMLTextAreaElement>('[data-testid="ai-chat-input"]')
    input.element.focus()
    await input.trigger('keydown', { key: 'Escape' })
    wrapper.get<HTMLButtonElement>('[data-testid="ai-chat-background-toggle"]').element.focus()
    await wrapper.vm.$nextTick()
    expect(wrapper.get('[data-testid="ai-chat-stop"]').text()).toBe('停止')
    input.element.focus()
    await input.trigger('keydown', { key: 'Escape' })
    expect(wrapper.emitted('stop')).toBeUndefined()
    await input.trigger('keydown', { key: 'Escape' })
    expect(wrapper.emitted('stop')).toHaveLength(1)
  })

  it.each(['window-blur', 'visibility', 'other-key', 'conversation', 'finished'])('%s 会取消等待第二次 Esc', async (change) => {
    const { wrapper } = mountComposer()
    await wrapper.setProps({ busy: true, conversationId: 'first' })
    const input = wrapper.get('[data-testid="ai-chat-input"]')
    await input.trigger('keydown', { key: 'Escape' })
    if (change === 'window-blur') window.dispatchEvent(new Event('blur'))
    if (change === 'visibility') document.dispatchEvent(new Event('visibilitychange'))
    if (change === 'other-key') await input.trigger('keydown', { key: 'a' })
    if (change === 'conversation') await wrapper.setProps({ conversationId: 'second' })
    if (change === 'finished') {
      await wrapper.setProps({ busy: false })
      await wrapper.setProps({ busy: true })
    }
    await wrapper.vm.$nextTick()
    expect(wrapper.get('[data-testid="ai-chat-stop"]').text()).toBe('停止')
    await input.trigger('keydown', { key: 'Escape' })
    expect(wrapper.emitted('stop')).toBeUndefined()
  })

  it('Esc 不冒泡收起浮层；空闲时仍可由外层处理，正在停止时不重复触发', async () => {
    const { wrapper } = mountComposer()
    const parentEscape = vi.fn()
    const container = wrapper.element.parentElement!
    container.addEventListener('keydown', parentEscape)
    try {
      await wrapper.setProps({ busy: true })
      const input = wrapper.get('[data-testid="ai-chat-input"]')
      await input.trigger('keydown', { key: 'Escape' })
      await input.trigger('keydown', { key: 'Escape' })
      expect(parentEscape).not.toHaveBeenCalled()
      await wrapper.setProps({ stopping: true })
      await input.trigger('keydown', { key: 'Escape' })
      await input.trigger('keydown', { key: 'Escape' })
      expect(wrapper.emitted('stop')).toHaveLength(1)
      expect(wrapper.get('[data-testid="ai-chat-stop"]').text()).toBe('正在停止…')
      await wrapper.setProps({ busy: false, stopping: false })
      await input.trigger('keydown', { key: 'Escape' })
      expect(parentEscape).toHaveBeenCalledTimes(1)
    } finally {
      container.removeEventListener('keydown', parentEscape)
    }
  })
})

describe('输入法组合输入', () => {
  it('组合期间 Enter 只交给输入法，结束后再次 Enter 才发送', async () => {
    const { wrapper } = mountComposer('pinyin')
    const input = wrapper.get('[data-testid="ai-chat-input"]')
    await input.trigger('compositionstart')
    const enter = new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true })
    input.element.dispatchEvent(enter)
    await wrapper.vm.$nextTick()
    expect(enter.defaultPrevented).toBe(false)
    expect(wrapper.emitted('send')).toBeUndefined()
    await wrapper.get('[data-testid="ai-chat-composer"]').trigger('submit')
    expect(wrapper.emitted('send')).toBeUndefined()
    await input.trigger('compositionend')
    await input.setValue('拼音')
    await input.trigger('keydown', { key: 'Enter' })
    expect(wrapper.emitted('send')).toHaveLength(1)
  })

  it.each([
    { isComposing: true },
    { isComposing: false, keyCode: 229 },
  ])('识别输入法键盘标记 %j，包括 compositionend 先于 Enter 的情况', async (flags) => {
    const { wrapper } = mountComposer('pinyin')
    const input = wrapper.get('[data-testid="ai-chat-input"]')
    await input.trigger('compositionstart')
    await input.trigger('compositionend')
    await input.trigger('keydown', { key: 'Enter', ...flags })
    expect(wrapper.emitted('send')).toBeUndefined()
    await input.trigger('keydown', { key: 'Enter' })
    expect(wrapper.emitted('send')).toHaveLength(1)
  })

  it('输入法期间不选择斜杠命令，也不把候选框 Esc 当作停止', async () => {
    const { store, wrapper } = mountComposer('/')
    const input = wrapper.get('[data-testid="ai-chat-input"]')
    await input.trigger('compositionstart')
    await input.trigger('keydown', { key: 'Enter' })
    await input.trigger('keydown', { key: 'Tab' })
    expect(store.activeConversationId).toBeNull()
    await wrapper.setProps({ busy: true })
    await input.trigger('keydown', { key: 'Escape' })
    await input.trigger('keydown', { key: 'Escape' })
    expect(wrapper.get('[data-testid="ai-chat-stop"]').text()).toBe('停止')
    expect(wrapper.emitted('stop')).toBeUndefined()
    await input.trigger('compositionend')
    await input.trigger('keydown', { key: 'Escape' })
    expect(wrapper.get('[data-testid="ai-chat-stop"]').text()).toBe('Esc')
    // 开始新的组合输入也会取消之前的 Esc 等待。
    await input.trigger('compositionstart')
    expect(wrapper.get('[data-testid="ai-chat-stop"]').text()).toBe('停止')
  })

  it('普通 Enter 发送，Shift + Enter 保留换行行为', async () => {
    const { wrapper } = mountComposer('消息')
    const input = wrapper.get('[data-testid="ai-chat-input"]')
    const newline = new KeyboardEvent('keydown', { key: 'Enter', shiftKey: true, bubbles: true, cancelable: true })
    input.element.dispatchEvent(newline)
    expect(newline.defaultPrevented).toBe(false)
    expect(wrapper.emitted('send')).toBeUndefined()
    await input.trigger('keydown', { key: 'Enter' })
    expect(wrapper.emitted('send')).toHaveLength(1)
  })
})
