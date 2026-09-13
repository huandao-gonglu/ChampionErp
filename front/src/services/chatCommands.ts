/** 会话快捷命令；审批直接使用原生工具卡。 */
export interface ChatCommandContext {
  isBusy: boolean
  startConversation: () => void
  stopStreaming: () => void
  refreshHistory: () => void
}
export interface ChatCommandMatch { command: ChatCommand; arg: string }
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

export const CHAT_COMMANDS: readonly ChatCommand[] = [
  { name: 'new', title: '新对话', description: '开始新对话', available: () => true,
    execute: context => { context.startConversation(); return true } },
  { name: 'cancel', title: '取消当前操作', description: '停止后续操作，已发出的平台请求按回执对账', available: () => true,
    execute: context => { context.stopStreaming(); return true } },
  { name: 'refresh', title: '刷新历史', description: '读取服务端已提交消息', available: () => true,
    execute: context => { context.refreshHistory(); return true } },
]

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
