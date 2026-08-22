import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  CHAT_COMMANDS,
  filterCommandsByQuery,
  matchChatCommand,
} from '../chatCommands'
import type { ChatCommandContext } from '../chatCommands'
import {
  approveGlobalTask,
  cancelGlobalTask,
  rejectGlobalTask,
} from '@/api/globalTasks'

vi.mock('@/api/globalTasks', () => ({
  approveGlobalTask: vi.fn(),
  rejectGlobalTask: vi.fn(),
  cancelGlobalTask: vi.fn(),
}))

function createContext(overrides: Partial<ChatCommandContext> = {}): ChatCommandContext {
  return {
    isBusy: false,
    hasUnresolvedTask: false,
    taskStatus: '',
    taskId: '',
    approvalStepId: '',
    approvalSummary: '',
    startConversation: vi.fn(),
    stopStreaming: vi.fn(),
    refreshTaskLink: vi.fn(async () => {}),
    ...overrides,
  }
}

function pendingApprovalContext(): ChatCommandContext {
  return createContext({
    hasUnresolvedTask: true,
    taskStatus: 'pending_approval',
    taskId: 'gtask-9',
    approvalStepId: 'step-1',
    approvalSummary: '删除 3 个商品',
  })
}

describe('命令注册中心', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(approveGlobalTask).mockResolvedValue({ ok: true, task_id: 'gtask-9', task: {} as never })
    vi.mocked(rejectGlobalTask).mockResolvedValue({ ok: true, task_id: 'gtask-9', task: {} as never })
    vi.mocked(cancelGlobalTask).mockResolvedValue({ ok: true, task_id: 'gtask-9', task: {} as never })
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('注册表即唯一命令清单：命令名唯一且为小写标识符', () => {
    const names = CHAT_COMMANDS.map((command) => command.name)
    expect(names).toEqual(['new', 'approve', 'reject', 'cancel'])
    expect(new Set(names).size).toBe(names.length)
    for (const command of CHAT_COMMANDS) {
      expect(command.name).toMatch(/^[a-z][a-z0-9_-]*$/)
      expect(command.title.trim()).not.toBe('')
      expect(command.description.trim()).not.toBe('')
    }
  })

  it('matchChatCommand 仅匹配已注册命令并解析尾随参数', () => {
    expect(matchChatCommand('/new')?.command.name).toBe('new')
    expect(matchChatCommand('  /new  ')?.command.name).toBe('new')
    // 未开启 takesArg 的命令保持精确匹配：`/new extra` 仍是普通消息。
    expect(matchChatCommand('/new extra')).toBeNull()
    expect(matchChatCommand('/reject')).toEqual(expect.objectContaining({ arg: '' }))
    expect(matchChatCommand('/reject 太危险')?.arg).toBe('太危险')
    expect(matchChatCommand('/unknown')).toBeNull()
    expect(matchChatCommand('new')).toBeNull()
    expect(matchChatCommand('')).toBeNull()
  })

  it('filterCommandsByQuery 只做前缀过滤：恒定返回全部命令，可见性交给 available', () => {
    // 即使空闲（无任务），面板也恒定列出全部命令，不可用项由 available 标灰。
    expect(filterCommandsByQuery('').map((command) => command.name))
      .toEqual(['new', 'approve', 'reject', 'cancel'])
  })

  it('available 决定命令是否可执行（面板中不可用即灰色禁用）：空闲时只有 /new 可用', () => {
    const context = createContext()
    const enabled = CHAT_COMMANDS.filter((command) => command.available(context))
    expect(enabled.map((command) => command.name)).toEqual(['new'])
  })

  it('available：待审批任务存在时审批类命令可用', () => {
    const enabled = CHAT_COMMANDS.filter((command) => command.available(pendingApprovalContext()))
    expect(enabled.map((command) => command.name)).toEqual(['new', 'approve', 'reject', 'cancel'])
  })

  it('available：任务执行中（in_progress）审批与取消命令不可用', () => {
    const context = createContext({ hasUnresolvedTask: true, taskStatus: 'in_progress', taskId: 'gtask-9' })
    const enabled = CHAT_COMMANDS.filter((command) => command.available(context))
    expect(enabled.map((command) => command.name)).toEqual(['new'])
  })

  it('filterCommandsByQuery 按名称前缀过滤且忽略大小写', () => {
    expect(filterCommandsByQuery('ne').map((c) => c.name)).toEqual(['new'])
    expect(filterCommandsByQuery('REJ').map((c) => c.name)).toEqual(['reject'])
    expect(filterCommandsByQuery('x')).toEqual([])
  })

  it('/new 执行时开始新会话并消费输入', () => {
    const context = createContext()
    const command = CHAT_COMMANDS.find((entry) => entry.name === 'new')!
    expect(command.execute(context, '')).toBe(true)
    expect(context.startConversation).toHaveBeenCalledOnce()
  })

  it('/approve 经确认后调用批准 API 并刷新任务关联', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const context = pendingApprovalContext()
    const command = CHAT_COMMANDS.find((entry) => entry.name === 'approve')!
    await expect(command.execute(context, '')).resolves.toBe(true)
    expect(approveGlobalTask).toHaveBeenCalledWith('gtask-9', 'step-1')
    expect(context.refreshTaskLink).toHaveBeenCalled()
  })

  it('/approve 在确认弹窗取消时不调用 API 也不消费输入', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    const context = pendingApprovalContext()
    const command = CHAT_COMMANDS.find((entry) => entry.name === 'approve')!
    await expect(command.execute(context, '')).resolves.toBe(false)
    expect(approveGlobalTask).not.toHaveBeenCalled()
  })

  it('/reject 缺少原因时不调用 API 也不消费输入', async () => {
    const context = pendingApprovalContext()
    const command = CHAT_COMMANDS.find((entry) => entry.name === 'reject')!
    await expect(command.execute(context, '   ')).resolves.toBe(false)
    expect(rejectGlobalTask).not.toHaveBeenCalled()
  })

  it('/reject 附原因时调用拒绝 API', async () => {
    const context = pendingApprovalContext()
    const command = CHAT_COMMANDS.find((entry) => entry.name === 'reject')!
    await expect(command.execute(context, '太危险')).resolves.toBe(true)
    expect(rejectGlobalTask).toHaveBeenCalledWith('gtask-9', 'step-1', '太危险')
  })

  it('/cancel 经确认后调用取消 API', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const context = pendingApprovalContext()
    const command = CHAT_COMMANDS.find((entry) => entry.name === 'cancel')!
    await expect(command.execute(context, '')).resolves.toBe(true)
    expect(cancelGlobalTask).toHaveBeenCalledWith('gtask-9')
  })
})
