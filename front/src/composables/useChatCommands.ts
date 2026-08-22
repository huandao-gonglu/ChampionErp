import { computed } from 'vue'
import { useAiChatStore } from '@/stores/aiChat'
import { filterCommandsByQuery } from '@/services/chatCommands'
import type { ChatCommand, ChatCommandContext } from '@/services/chatCommands'

/** 面板条目：命令本身 + 当前状态下是否可执行（不可用时灰色禁用展示）。 */
export interface ChatCommandEntry {
  command: ChatCommand
  enabled: boolean
}

/**
 * 命令面板与 aiChat store 之间的桥接。
 *
 * 命令的定义与可见性规则集中在 `services/chatCommands`（统一注册点），
 * 这里只负责从 store 当前状态构建注册表所需的上下文，并提供：
 * - `commandsFor(query)`：按 `/` 之后的查询串返回命令条目（含可用性标记）；
 * - `selectCommand(command)`：面板选中动作。
 */
export function useChatCommands() {
  const chatStore = useAiChatStore()

  const context = computed<ChatCommandContext>(() => {
    const linkedTask = chatStore.taskLink?.task ?? null
    return {
      isBusy: chatStore.isBusy,
      hasUnresolvedTask: chatStore.hasUnresolvedTask,
      taskStatus: linkedTask?.status ?? '',
      taskId: chatStore.taskLink?.task_id ?? '',
      approvalStepId: linkedTask?.pending_approval?.step_id ?? '',
      approvalSummary: String(linkedTask?.pending_approval?.payload?.summary ?? '').trim(),
      startConversation: chatStore.startConversation,
      stopStreaming: chatStore.stopStreaming,
      refreshTaskLink: chatStore.refreshTaskLink,
    }
  })

  /** 按查询串（`/` 之后的部分）返回命令条目；查询串为空返回全部命令。 */
  function commandsFor(query: string): ChatCommandEntry[] {
    return filterCommandsByQuery(query).map((command) => ({
      command,
      enabled: command.available(context.value),
    }))
  }

  /**
   * 面板选中命令：
   * - 当前不可用的命令直接忽略（面板以灰色禁用态展示，不附原因）；
   * - 需要尾随参数的命令（如 `/reject 原因`）只把输入预填为 `/name `，
   *   由用户继续补充参数后发送；
   * - 其他命令直接复用 store 的发送分发路径执行（等价于输入 `/name` 并发送）。
   */
  function selectCommand(command: ChatCommand): void {
    if (!command.available(context.value)) return
    if (command.takesArg) {
      chatStore.input = `/${command.name} `
      return
    }
    chatStore.input = `/${command.name}`
    chatStore.sendMessage()
  }

  return { commandsFor, selectCommand }
}
