import { createPinia, setActivePinia } from 'pinia'
import { mount } from '@vue/test-utils'
import type { VueWrapper } from '@vue/test-utils'
import { beforeEach, describe, expect, it } from 'vitest'
import AiChatComposer from '../AiChatComposer.vue'
import { useAiChatStore } from '@/stores/aiChat'
import type { ConversationTaskLinkResponse } from '@/types/aiWork'

function pendingApprovalTaskLink(): ConversationTaskLinkResponse {
  return {
    ok: true,
    conversation_id: 'conversation_global_chat_test',
    task_id: 'gtask-9',
    link_status: 'ready',
    task: {
      task_id: 'gtask-9',
      goal: '删除商品',
      status: 'pending_approval',
      steps: [],
      current_step_index: 0,
      pending_approval: {
        step_id: 'step-1',
        capability_name: 'product_delete',
        capability_version: '1',
        task_revision: 1,
        digest: 'digest',
        payload: { summary: '删除 3 个商品' },
        requested_at: '',
      },
      pending_inputs: [],
    },
  }
}

function mountComposer(initialInput = '') {
  const pinia = createPinia()
  setActivePinia(pinia)
  const store = useAiChatStore()
  store.input = initialInput
  const wrapper = mount(AiChatComposer, {
    global: {
      plugins: [pinia],
      stubs: { TaskApprovalModeSelect: true },
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
  return { store, wrapper }
}

async function typeInput(wrapper: VueWrapper, value: string) {
  await wrapper.get('[data-testid="ai-chat-input"]').setValue(value)
}

describe('AiChatComposer 斜杠命令面板', () => {
  beforeEach(() => {
    // pinia 在每个用例内由 mountComposer 重建。
  })

  it('输入 / 时弹出命令面板：恒定列出全部命令，不可用项灰色禁用', async () => {
    const { wrapper } = mountComposer()
    await typeInput(wrapper, '/')

    const panel = wrapper.find('[data-testid="ai-chat-command-panel"]')
    expect(panel.exists()).toBe(true)
    // 空闲会话：/new 可用，其余命令灰色禁用（不附原因）。
    expect(wrapper.find('[data-testid="ai-chat-command-new"]').attributes('aria-disabled')).toBeUndefined()
    expect(wrapper.find('[data-testid="ai-chat-command-approve"]').attributes('aria-disabled')).toBe('true')
    expect(wrapper.find('[data-testid="ai-chat-command-reject"]').attributes('aria-disabled')).toBe('true')
    expect(wrapper.find('[data-testid="ai-chat-command-cancel"]').attributes('aria-disabled')).toBe('true')
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

  it('ArrowDown / ArrowUp 在候选命令间循环移动高亮', async () => {
    const { store, wrapper } = mountComposer()
    store.taskLink = pendingApprovalTaskLink()
    await typeInput(wrapper, '/')

    const input = wrapper.get('[data-testid="ai-chat-input"]')
    expect(wrapper.find('[data-testid="ai-chat-command-new"]').attributes('aria-selected')).toBe('true')

    await input.trigger('keydown', { key: 'ArrowDown' })
    expect(wrapper.find('[data-testid="ai-chat-command-approve"]').attributes('aria-selected')).toBe('true')

    // 从首项向上循环到末项。
    await input.trigger('keydown', { key: 'ArrowUp' })
    await input.trigger('keydown', { key: 'ArrowUp' })
    expect(wrapper.find('[data-testid="ai-chat-command-cancel"]').attributes('aria-selected')).toBe('true')
  })

  it('键盘导航跳过禁用项：空闲时高亮始终停留在唯一可用的 /new', async () => {
    const { wrapper } = mountComposer()
    await typeInput(wrapper, '/')
    const input = wrapper.get('[data-testid="ai-chat-input"]')

    await input.trigger('keydown', { key: 'ArrowDown' })
    expect(wrapper.find('[data-testid="ai-chat-command-new"]').attributes('aria-selected')).toBe('true')
    await input.trigger('keydown', { key: 'ArrowUp' })
    expect(wrapper.find('[data-testid="ai-chat-command-new"]').attributes('aria-selected')).toBe('true')
  })

  it('点击禁用命令无效果：不执行也不改变输入', async () => {
    const { store, wrapper } = mountComposer()
    await typeInput(wrapper, '/')

    await wrapper.get('[data-testid="ai-chat-command-approve"]').trigger('click')

    expect(store.input).toBe('/')
    expect(store.activeConversationId).toBeNull()
  })

  it('Escape 关闭面板并保留输入', async () => {
    const { store, wrapper } = mountComposer()
    await typeInput(wrapper, '/')
    expect(wrapper.find('[data-testid="ai-chat-command-panel"]').exists()).toBe(true)

    await wrapper.get('[data-testid="ai-chat-input"]').trigger('keydown', { key: 'Escape' })

    expect(wrapper.find('[data-testid="ai-chat-command-panel"]').exists()).toBe(false)
    expect(store.input).toBe('/')
  })

  it('点击需要参数的命令只预填输入，等待用户补充参数', async () => {
    const { store, wrapper } = mountComposer()
    store.taskLink = pendingApprovalTaskLink()
    await typeInput(wrapper, '/')

    await wrapper.get('[data-testid="ai-chat-command-reject"]').trigger('click')

    expect(store.input).toBe('/reject ')
  })

  it('busy 时不显示命令面板', async () => {
    const { wrapper } = mountComposer('/')
    await wrapper.setProps({ busy: true })
    expect(wrapper.find('[data-testid="ai-chat-command-panel"]').exists()).toBe(false)
  })
})
