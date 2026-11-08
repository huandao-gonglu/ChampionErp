import type { UIMessage } from 'ai'
import type { AiTurnTiming, AiUiPart, PendingToolCall } from '@/types/aiWork'

export interface MessageGroup {
  id: string
  role: UIMessage['role']
  parts: AiUiPart[]
  messageId: string
  timing?: AiTurnTiming
}

/** 仅派生展示分组，不改写 SDK 消息；用户补充消息始终构成新的边界。 */
export function groupAiMessages(messages: UIMessage[]): MessageGroup[] {
  const groups: MessageGroup[] = []
  let boundaryId = ''
  let timing: AiTurnTiming | undefined
  for (const message of messages) {
    const metadata = message.metadata as { ai_turn?: AiTurnTiming } | undefined
    if (message.role === 'user') timing = metadata?.ai_turn
    else if (metadata?.ai_turn) timing = metadata.ai_turn
    const previous = groups.at(-1)
    if (message.role === 'assistant' && previous?.role === 'assistant') {
      previous.parts.push(...message.parts)
      previous.messageId = message.id
      previous.timing = timing
    } else {
      groups.push({
        id: message.role === 'assistant' ? `reply:${boundaryId || message.id}` : message.id,
        role: message.role,
        parts: [...message.parts],
        messageId: message.id,
        timing,
      })
    }
    if (message.role !== 'assistant') boundaryId = message.id
  }
  return groups
}

export interface ToolPartView {
  type: string
  toolName?: string
  toolCallId?: string
  state?: string
  input?: unknown
  output?: unknown
  errorText?: string
}

export function isAiToolPart(part: AiUiPart): boolean {
  return part.type === 'dynamic-tool' || part.type.startsWith('tool-')
}

export function aiToolName(part: AiUiPart): string {
  const tool = part as ToolPartView
  return tool.toolName || tool.type.replace(/^tool-/, '')
}

export type ToolDisplayStatus = 'preparing' | 'running' | 'approval' | 'waiting' | 'input'
  | 'unknown' | 'failed' | 'partial' | 'denied' | 'completed' | 'submitted' | 'script_error'

function record(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object' ? value as Record<string, unknown> : {}
}

/** 未返回的历史调用不等于审批，也不能因为传输已结束就当作成功。 */
export function aiToolStatus(part: AiUiPart, pendingKind?: PendingToolCall['kind'], active = false): ToolDisplayStatus {
  const tool = part as ToolPartView
  const output = record(tool.output)
  const error = record(output.error)
  if (output.outcome_unknown === true || record(output.details).outcome_unknown === true
    || record(error.details).outcome_unknown === true || output.status === 'unknown') return 'unknown'
  if (tool.state === 'output-denied') return 'denied'
  if (output.ok === false || output.status === 'failed') return 'failed'
  if (tool.state === 'output-error') {
    // Code Mode 原生 ModelRetry 将执行前诊断放在 errorText，没有结构化错误类别。
    // 只识别其固定前缀；运行时错误可能已有副作用，仍作为失败展示。
    if (aiToolName(part) === 'run_code' && /^(?:Type|Syntax) error in code:\r?\n/.test(tool.errorText || '')) return 'script_error'
    return 'failed'
  }
  if (output.status === 'partial') return 'partial'
  if (tool.state === 'output-available') {
    if (['pending', 'queued', 'running', 'processing'].includes(String(output.status))) return 'submitted'
    return 'completed'
  }
  if (pendingKind === 'input') return 'input'
  if (pendingKind === 'approval') return 'approval'
  if (pendingKind === 'external') return 'running'
  if (active) return tool.state === 'input-streaming' ? 'preparing' : 'waiting'
  return 'unknown'
}

export function toolNeedsAttention(status: ToolDisplayStatus): boolean {
  return ['approval', 'unknown', 'failed', 'partial', 'denied'].includes(status)
}

export interface DisplayPart {
  key: string
  part: AiUiPart
  pendingKind?: PendingToolCall['kind']
}

/** 实时的多个 step 和历史的多个 response 使用相同规则，保留原始顺序和引用。 */
export function presentAssistantParts(parts: AiUiPart[], pending: PendingToolCall[] = [], active = false) {
  const pendingById = new Map(pending.map(call => [call.tool_call_id, call.kind]))
  const entries: DisplayPart[] = parts.filter(part => part.type !== 'step-start').map((part, index) => ({
    key: isAiToolPart(part) && 'toolCallId' in part ? `tool:${part.toolCallId}` : `part:${index}`,
    part,
    pendingKind: 'toolCallId' in part ? pendingById.get(String(part.toolCallId)) : undefined,
  }))
  // 待审批或后台执行时仍保留完整时间线；结束后才派生折叠的过程。
  const live = active || entries.some(entry => entry.pendingKind !== undefined)
  const timeline = entries.filter(({ part }) => part.type !== 'text' || part.text.trim())
  // 不按文本内容猜测“最终答案”，也不丢弃中途问题、附件或来源。
  let lastTool = -1
  entries.forEach((entry, index) => { if (isAiToolPart(entry.part)) lastTool = index })
  const process: DisplayPart[] = []
  const attention: DisplayPart[] = []
  const response: DisplayPart[] = []
  let hasActiveTool = false
  for (const [index, entry] of entries.entries()) {
    const { part, pendingKind } = entry
    if (isAiToolPart(part)) {
      const status = aiToolStatus(part, pendingKind, live)
      if (toolNeedsAttention(status)) attention.push(entry)
      else process.push(entry)
      if (['preparing', 'waiting', 'running', 'approval'].includes(status)) hasActiveTool = true
    } else if (part.type === 'text') {
      if (!part.text.trim()) continue
      if (index < lastTool) process.push(entry)
      else response.push(entry)
    } else if (part.type === 'file' || part.type === 'source-url' || part.type === 'source-document') {
      response.push(entry)
    } else {
      process.push(entry)
    }
  }
  return { live, timeline, process, attention, response, hasActiveTool }
}
