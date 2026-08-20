import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { AiUiPart } from '@/types/aiWork'
import {
  approveGlobalTask,
  fetchGlobalTask,
  refreshGlobalTask,
  rejectGlobalTask,
} from '@/api/globalTasks'
import AiMessagePart from '../AiMessagePart.vue'

vi.mock('@/api/globalTasks', () => ({
  approveGlobalTask: vi.fn(),
  fetchGlobalTask: vi.fn(),
  refreshGlobalTask: vi.fn(),
  rejectGlobalTask: vi.fn(),
}))

function mountPart(part: Record<string, unknown>, taskActionsEnabled = false) {
  return mount(AiMessagePart, {
    props: { part: part as AiUiPart, taskActionsEnabled },
  })
}

describe('AiMessagePart', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('渲染 text 与默认折叠的 reasoning', () => {
    const text = mountPart({ type: 'text', text: '回答正文' })
    expect(text.get('[data-testid="ai-part-text"]').text()).toBe('回答正文')

    const reasoning = mountPart({
      type: 'reasoning',
      text: '分析过程',
      state: 'streaming',
    })
    expect(reasoning.get('[data-testid="ai-part-reasoning"] summary').text()).toBe('思考中…')
    expect(reasoning.text()).toContain('分析过程')
  })

  it('按状态渲染工具卡', () => {
    const wrapper = mountPart({
      type: 'tool-drafts_query',
      toolCallId: 'call-1',
      state: 'output-available',
      input: { scope: 'active' },
      output: { total: 2 },
    })

    const card = wrapper.get('[data-testid="ai-part-tool"]')
    expect(card.text()).toContain('drafts_query')
    expect(card.text()).toContain('工具完成')
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

  it('未知 part 折叠为调试信息且不影响消息', () => {
    const wrapper = mountPart({ type: 'data-custom', data: { value: 1 } })
    expect(wrapper.get('[data-testid="ai-part-debug"]').text()).toContain('data-custom')
    expect(wrapper.text()).toContain('"value": 1')
  })

  it('在活动全局对话中展示服务端审批摘要并执行批准', async () => {
    const pending = {
      ok: true,
      task_id: 'gtask-delete-1',
      task: {
        task_id: 'gtask-delete-1',
        goal: '删除指定商品',
        status: 'pending_approval',
        current_step_index: 0,
        steps: [{
          step_id: 'step-1',
          capability_name: 'product_delete',
          status: 'pending',
        }],
        pending_approval: {
          step_id: 'step-1',
          capability_name: 'product_delete',
          capability_version: '1',
          task_revision: 2,
          digest: 'digest-1',
          requested_at: '2026-08-19T12:00:00Z',
          payload: {
            summary: '删除 2 个本地商品：product-1、product-2',
            canonical_payload: { product_ids: ['product-1', 'product-2'] },
          },
        },
      },
    }
    const completed = {
      ...pending,
      task: {
        ...pending.task,
        status: 'completed',
        pending_approval: null,
        steps: [{
          ...pending.task.steps[0],
          status: 'completed',
          result: { deleted: 2 },
        }],
        assistant_message: '任务已完成。',
      },
    }
    vi.mocked(fetchGlobalTask).mockResolvedValue(pending as never)
    vi.mocked(approveGlobalTask).mockResolvedValue(completed as never)
    vi.spyOn(window, 'confirm').mockReturnValue(true)

    const wrapper = mountPart({
      type: 'tool-global_task_start',
      toolCallId: 'call-delete',
      state: 'output-available',
      output: pending,
    }, true)
    await vi.waitFor(() => {
      expect(fetchGlobalTask).toHaveBeenCalledWith('gtask-delete-1')
    })

    const card = wrapper.get('[data-testid="global-task-card"]')
    expect(card.text()).toContain('等待你的审批')
    expect(card.text()).toContain('删除 2 个本地商品')

    await wrapper.get('[data-testid="global-task-approve"]').trigger('click')
    await vi.waitFor(() => {
      expect(approveGlobalTask).toHaveBeenCalledWith('gtask-delete-1', 'step-1')
    })
    expect(wrapper.get('[data-testid="global-task-card"]').text()).toContain('已完成')
    expect(rejectGlobalTask).not.toHaveBeenCalled()
  })

  it('只读消息展示审批摘要但不提供审批按钮', () => {
    const wrapper = mountPart({
      type: 'tool-global_task_start',
      state: 'output-available',
      output: {
        ok: true,
        task_id: 'gtask-readonly',
        task: {
          task_id: 'gtask-readonly',
          goal: '删除草稿',
          status: 'pending_approval',
          current_step_index: 0,
          steps: [],
          pending_approval: {
            step_id: 'step-1',
            capability_name: 'draft_delete',
            capability_version: '1',
            task_revision: 2,
            digest: 'digest-2',
            requested_at: '2026-08-19T12:00:00Z',
            payload: { summary: '删除一个草稿' },
          },
        },
      },
    })

    expect(wrapper.text()).toContain('只读消息不能审批')
    expect(wrapper.find('[data-testid="global-task-approve"]').exists()).toBe(false)
    expect(fetchGlobalTask).not.toHaveBeenCalled()
  })

  it('后台任务刷新会推进任务状态而不只读取旧快照', async () => {
    const running = {
      ok: true,
      task_id: 'gtask-running',
      task: {
        task_id: 'gtask-running',
        goal: '等待后台发布',
        status: 'in_progress',
        current_step_index: 0,
        steps: [],
      },
    }
    vi.mocked(refreshGlobalTask).mockResolvedValue({
      ...running,
      task: {
        ...running.task,
        status: 'completed',
        assistant_message: '后台任务已完成。',
      },
    } as never)

    const wrapper = mountPart({
      type: 'tool-global_task_start',
      state: 'output-available',
      output: running,
    }, true)

    await vi.waitFor(() => {
      expect(refreshGlobalTask).toHaveBeenCalledWith('gtask-running')
      expect(wrapper.get('[data-testid="global-task-card"]').text()).toContain('已完成')
    })
    expect(fetchGlobalTask).not.toHaveBeenCalled()
  })

  it('分步任务处于 running 时刷新会继续执行下一步', async () => {
    const running = {
      ok: true,
      task_id: 'gtask-checkpointed',
      task: {
        task_id: 'gtask-checkpointed',
        goal: '依次准备多个市场',
        status: 'running',
        current_step_index: 1,
        steps: [
          { step_id: 'step-1', status: 'completed' },
          { step_id: 'step-2', status: 'pending' },
        ],
      },
    }
    vi.mocked(refreshGlobalTask).mockResolvedValue({
      ...running,
      task: {
        ...running.task,
        status: 'completed',
        current_step_index: 2,
        steps: running.task.steps.map((step) => ({ ...step, status: 'completed' })),
      },
    } as never)

    const wrapper = mountPart({
      type: 'tool-global_task_start',
      state: 'output-available',
      output: running,
    }, true)

    await vi.waitFor(() => {
      expect(refreshGlobalTask).toHaveBeenCalledWith('gtask-checkpointed')
      expect(wrapper.get('[data-testid="global-task-card"]').text()).toContain('已完成')
    })
    expect(fetchGlobalTask).not.toHaveBeenCalled()
  })
})
