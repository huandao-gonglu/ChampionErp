import { approveGlobalTask, cancelGlobalTask, rejectGlobalTask } from '@/api/globalTasks'
import type { GlobalTaskStatus } from '@/types/aiWork'

/**
 * 全局对话斜杠命令注册中心（统一注册扫描点）。
 *
 * 所有 agent 会话级命令只在这里声明一次：候选面板（useChatCommands /
 * AiChatCommandPanel）与发送分发（aiChat store 的 sendMessage）都消费
 * `CHAT_COMMANDS`，新增命令只需在本文件追加一条定义即可被自动收录，
 * 不需要在 UI 或 store 里另行枚举。
 *
 * 命令不新增后端接口：执行逻辑复用既有 store 动作与任务 API。
 * 本模块不 import store，以避免注册中心与 store 之间的循环依赖；
 * 所需状态与动作通过调用方构建的 `ChatCommandContext` 注入。
 */

/** 命令执行所需的状态快照与动作，由调用方（store / composable）构建。 */
export interface ChatCommandContext {
  isBusy: boolean
  hasUnresolvedTask: boolean
  /** 关联任务状态；任务快照缺失时为空字符串。 */
  taskStatus: GlobalTaskStatus | ''
  taskId: string
  approvalStepId: string
  approvalSummary: string
  startConversation: () => void
  stopStreaming: () => void
  refreshTaskLink: () => Promise<void>
}

export interface ChatCommand {
  /** 命令名，输入形式为 `/<name>`。 */
  name: string
  /** 面板展示的简短标题。 */
  title: string
  /** 面板展示的一句话说明。 */
  description: string
  /** 允许尾随参数（如 `/reject 原因`）；未开启时仅精确匹配 `/<name>`。 */
  takesArg?: boolean
  /** 条件可见性：返回 false 时不出现在面板，也不会被分发执行。 */
  available: (context: ChatCommandContext) => boolean
  /** 执行命令；返回 true 表示已消费、应清空输入；false 保留输入（如用户取消确认、缺少参数）。 */
  execute: (context: ChatCommandContext, arg: string) => boolean | Promise<boolean>
}

const TERMINAL_TASK_STATUSES: readonly GlobalTaskStatus[] = ['completed', 'failed', 'cancelled']

function hasPendingApproval(context: ChatCommandContext): boolean {
  return context.taskStatus === 'pending_approval' && context.approvalStepId !== ''
}

/** 已注册的会话级命令；顺序即面板展示顺序。 */
export const CHAT_COMMANDS: readonly ChatCommand[] = [
  {
    name: 'new',
    title: '新对话',
    description: '清空当前输入并开始新的空白全局对话，不发送消息',
    available: () => true,
    execute: (context) => {
      context.startConversation()
      return true
    },
  },
  {
    name: 'approve',
    title: '批准任务',
    description: '批准当前等待审批的高风险操作',
    available: hasPendingApproval,
    execute: async (context) => {
      if (!context.taskId || !context.approvalStepId) return false
      const summary = context.approvalSummary || `任务 ${context.taskId} 的当前步骤`
      if (!window.confirm(`确认执行以下高风险操作？\n\n${summary}`)) return false
      try {
        await approveGlobalTask(context.taskId, context.approvalStepId)
      } catch {
        return false
      }
      await context.refreshTaskLink()
      return true
    },
  },
  {
    name: 'reject',
    title: '拒绝任务',
    description: '拒绝当前等待审批的操作，需附原因：/reject 原因',
    takesArg: true,
    available: hasPendingApproval,
    execute: async (context, arg) => {
      const reason = arg.trim()
      if (!context.taskId || !context.approvalStepId || !reason) return false
      try {
        await rejectGlobalTask(context.taskId, context.approvalStepId, reason)
      } catch {
        return false
      }
      await context.refreshTaskLink()
      return true
    },
  },
  {
    name: 'cancel',
    title: '取消任务',
    description: '取消进行中的全局任务，已执行的步骤不会撤销',
    available: (context) => (
      context.hasUnresolvedTask
      && context.taskStatus !== 'in_progress'
      && !TERMINAL_TASK_STATUSES.includes(context.taskStatus as GlobalTaskStatus)
    ),
    execute: async (context) => {
      if (!context.taskId) return false
      if (!window.confirm('确认取消该任务？已执行的步骤不会被撤销。')) return false
      try {
        await cancelGlobalTask(context.taskId)
      } catch {
        return false
      }
      await context.refreshTaskLink()
      return true
    },
  },
]

export interface ChatCommandMatch {
  command: ChatCommand
  arg: string
}

/**
 * 把输入文本匹配到已注册命令。
 * 未开启 takesArg 的命令仅精确匹配 `/<name>`（保持 `/new extra` 仍作为
 * 普通消息发送的既有语义）；开启的命令允许 `/<name> 参数`。
 */
export function matchChatCommand(text: string): ChatCommandMatch | null {
  const trimmed = text.trim()
  if (!trimmed.startsWith('/')) return null
  for (const command of CHAT_COMMANDS) {
    const prefix = `/${command.name}`
    if (trimmed === prefix) return { command, arg: '' }
    if (command.takesArg && trimmed.startsWith(`${prefix} `)) {
      return { command, arg: trimmed.slice(prefix.length + 1) }
    }
  }
  return null
}

/**
 * 按 `/` 之后的查询串过滤命令（名称前缀匹配，忽略大小写），不做可见性筛选。
 * 查询串为空时返回全部命令。面板会恒定展示匹配到的命令，`available` 为
 * false 的以灰色禁用态呈现（不附原因）；发送分发仍只执行可用命令。
 */
export function filterCommandsByQuery(query: string): ChatCommand[] {
  const normalized = query.trim().toLowerCase()
  return CHAT_COMMANDS.filter((command) => (
    normalized === '' || command.name.toLowerCase().startsWith(normalized)
  ))
}
