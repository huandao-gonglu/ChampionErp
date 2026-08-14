import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import AiWorkView from '../AiWorkView.vue'

const mocks = vi.hoisted(() => ({
  route: { query: {} as Record<string, unknown> },
  fetchConversations: vi.fn(),
  fetchConversation: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRoute: () => mocks.route,
}))

vi.mock('@/api/aiWork', () => ({
  fetchPydanticConversations: mocks.fetchConversations,
  fetchPydanticConversation: mocks.fetchConversation,
}))

const conversations = [
  {
    conversation_id: 'conversation-1',
    created_at: '2026-08-14T08:00:00+08:00',
    updated_at: '2026-08-14T08:02:00+08:00',
  },
  {
    conversation_id: 'conversation-2',
    created_at: '2026-08-14T09:00:00+08:00',
    updated_at: '2026-08-14T09:03:00+08:00',
  },
]

function detail(conversationId: string) {
  return {
    ok: true,
    conversation_id: conversationId,
    created_at: `2026-08-14T0${conversationId.endsWith('1') ? '8' : '9'}:00:00+08:00`,
    updated_at: `2026-08-14T0${conversationId.endsWith('1') ? '8' : '9'}:03:00+08:00`,
    messages: [
      {
        kind: 'request',
        parts: [{ part_kind: 'user-prompt', content: `原始内容-${conversationId}` }],
      },
      {
        kind: 'response',
        parts: [{ part_kind: 'text', content: '未经解释的响应' }],
      },
    ],
  }
}

describe('AiWorkView Pydantic JSON 检查器', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.route.query = {}
    mocks.fetchConversations.mockResolvedValue({ ok: true, conversations })
    mocks.fetchConversation.mockImplementation(async (conversationId: string) => detail(conversationId))
  })

  it('加载列表、选择首项并以通用 JSON tree 展示原值', async () => {
    const wrapper = mount(AiWorkView)
    await flushPromises()

    expect(mocks.fetchConversations).toHaveBeenCalledWith()
    expect(mocks.fetchConversation).toHaveBeenCalledWith('conversation-1')
    expect(wrapper.get('[data-testid="ai-work-selected-id"]').text()).toBe('conversation-1')
    const tree = wrapper.get('[data-testid="ai-work-json-tree"]')
    expect(tree.text()).toContain('messages')
    expect(tree.text()).toContain('Array(2)')
    expect(tree.text()).toContain('part_kind')
    expect(tree.text()).toContain('原始内容-conversation-1')
    expect(wrapper.text()).not.toContain('用户目标')
    expect(wrapper.text()).not.toContain('处理结果')
  })

  it('切换 conversation 后显示对应详情和原始 JSON', async () => {
    const wrapper = mount(AiWorkView)
    await flushPromises()

    await wrapper.get('[data-testid="ai-work-conversation-conversation-2"]').trigger('click')
    await flushPromises()
    await wrapper.get('[data-testid="ai-work-raw-tab"]').trigger('click')

    expect(mocks.fetchConversation).toHaveBeenLastCalledWith('conversation-2')
    expect(wrapper.get('[data-testid="ai-work-selected-id"]').text()).toBe('conversation-2')
    const raw = wrapper.get('[data-testid="ai-work-raw-json"]').text()
    expect(raw).toContain('"kind": "request"')
    expect(raw).toContain('原始内容-conversation-2')
  })

  it('手动刷新列表和当前详情，不启动自动轮询', async () => {
    const wrapper = mount(AiWorkView)
    await flushPromises()

    await wrapper.get('[data-testid="ai-work-refresh"]').trigger('click')
    await flushPromises()

    expect(mocks.fetchConversations).toHaveBeenCalledTimes(2)
    expect(mocks.fetchConversation).toHaveBeenCalledTimes(2)
    expect(mocks.fetchConversation).toHaveBeenLastCalledWith('conversation-1')
  })

  it('可通过 query 直接打开指定 conversation', async () => {
    mocks.route.query = { conversation_id: 'conversation-2' }

    const wrapper = mount(AiWorkView)
    await flushPromises()

    expect(mocks.fetchConversation).toHaveBeenCalledWith('conversation-2')
    expect(wrapper.get('[data-testid="ai-work-selected-id"]').text()).toBe('conversation-2')
  })

  it('messages 不是数组时显示清晰的 JSON 验证错误', async () => {
    mocks.fetchConversation.mockResolvedValue({
      ...detail('conversation-1'),
      messages: { invalid: true },
    })

    const wrapper = mount(AiWorkView)
    await flushPromises()

    expect(wrapper.get('[data-testid="ai-work-json-error"]').text()).toContain(
      '消息 JSON 格式无效：messages 必须是 JSON 数组。',
    )
    expect(wrapper.find('[data-testid="ai-work-json-tree"]').exists()).toBe(false)
  })

  it('list 与 detail 请求失败时分别展示读取错误', async () => {
    mocks.fetchConversations.mockRejectedValueOnce(new Error('索引不可用'))
    const listWrapper = mount(AiWorkView)
    await flushPromises()
    expect(listWrapper.get('[role="alert"]').text()).toContain('读取 conversation 列表失败：索引不可用')
    listWrapper.unmount()

    mocks.fetchConversations.mockResolvedValue({ ok: true, conversations })
    mocks.fetchConversation.mockRejectedValueOnce(new Error('详情不可用'))
    const detailWrapper = mount(AiWorkView)
    await flushPromises()
    expect(detailWrapper.get('[data-testid="ai-work-detail-error"]').text()).toContain(
      '读取 conversation 失败：详情不可用',
    )
  })

  it('从已加载的 messages 生成下载文件，不请求 raw 端点', async () => {
    const createObjectUrl = vi.fn(() => 'blob:ai-work')
    const revokeObjectUrl = vi.fn()
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: createObjectUrl })
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: revokeObjectUrl })
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    const wrapper = mount(AiWorkView)
    await flushPromises()

    await wrapper.get('[data-testid="ai-work-download"]').trigger('click')

    expect(createObjectUrl).toHaveBeenCalledOnce()
    expect(revokeObjectUrl).toHaveBeenCalledWith('blob:ai-work')
    expect(click).toHaveBeenCalledOnce()
    click.mockRestore()
    Reflect.deleteProperty(URL, 'createObjectURL')
    Reflect.deleteProperty(URL, 'revokeObjectURL')
  })
})
