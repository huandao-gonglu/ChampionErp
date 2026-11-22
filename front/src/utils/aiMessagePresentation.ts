import type { UIMessage } from 'ai'
import type { AiTurnTiming, AiUiPart, BusinessWriteReceipt, ManualAiAction, PendingToolCall } from '@/types/aiWork'

export interface MessageGroup {
  id: string
  role: UIMessage['role']
  parts: AiUiPart[]
  messageId: string
  timing?: AiTurnTiming
  manualAction?: ManualAiAction
  writeReceipts?: BusinessWriteReceipt[]
}

/** 仅派生展示分组，不改写 SDK 消息；用户补充消息始终构成新的边界。 */
export function groupAiMessages(messages: UIMessage[]): MessageGroup[] {
  const groups: MessageGroup[] = []
  let boundaryId = ''
  let timing: AiTurnTiming | undefined
  for (const message of messages) {
    const metadata = message.metadata as {
      ai_turn?: AiTurnTiming
      manual_ai_action?: ManualAiAction
      business_write_receipts?: BusinessWriteReceipt[]
    } | undefined
    const receipts = Array.isArray(metadata?.business_write_receipts) ? metadata.business_write_receipts : []
    if (message.role === 'user') timing = metadata?.ai_turn
    else if (metadata?.ai_turn) timing = metadata.ai_turn
    const previous = groups.at(-1)
    if (message.role === 'assistant' && previous?.role === 'assistant') {
      previous.parts.push(...message.parts)
      if (receipts.length) previous.writeReceipts = [...(previous.writeReceipts ?? []), ...receipts]
      previous.messageId = message.id
      previous.timing = timing
    } else {
      groups.push({
        id: message.role === 'assistant' ? `reply:${boundaryId || message.id}` : message.id,
        role: message.role,
        parts: [...message.parts],
        messageId: message.id,
        timing,
        manualAction: metadata?.manual_ai_action,
        ...(receipts.length ? { writeReceipts: [...receipts] } : {}),
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
  | 'unknown' | 'failed' | 'partial' | 'denied' | 'completed' | 'submitted' | 'script_error' | 'desktop'

function record(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object' ? value as Record<string, unknown> : {}
}

const DESKTOP_IMAGE_TOOLS = new Set(['image_translate', 'image_edit', 'draft_images_prepare'])

function desktopImageActionFromOutput(toolName: string, toolCallId: string | undefined, output: unknown) {
  if (!DESKTOP_IMAGE_TOOLS.has(toolName)) return
  const error = record(record(output).error)
  const action = record(record(error.details).desktop_image_action)
  if (error.code === 'IMAGE_DESKTOP_REQUIRED' && typeof action.conversation_id === 'string'
    && typeof action.tool_call_id === 'string' && action.tool_call_id === toolCallId) {
    return { conversation_id: action.conversation_id, tool_call_id: action.tool_call_id }
  }
}

export function desktopImageAction(part: AiUiPart) {
  const tool = part as ToolPartView
  return desktopImageActionFromOutput(aiToolName(part), tool.toolCallId, tool.output)
}

function desktopImageReceiptParts(receipts: BusinessWriteReceipt[], seenToolCallIds: Set<string>): DisplayPart[] {
  const deduped = new Map<string, DisplayPart>()
  for (const receipt of receipts) {
    if (seenToolCallIds.has(receipt.tool_call_id)) continue
    if (!desktopImageActionFromOutput(receipt.tool_name, receipt.tool_call_id, receipt.output)) continue
    const details = record(record(record(receipt.output).error).details)
    const request = details.request
    const key = request && typeof request === 'object'
      ? JSON.stringify([receipt.tool_name, request])
      : receipt.tool_call_id
    deduped.set(key, {
      key: `receipt:${receipt.tool_call_id}`,
      part: {
        type: 'dynamic-tool',
        toolName: receipt.tool_name,
        toolCallId: receipt.tool_call_id,
        state: 'output-available',
        ...(request !== undefined ? { input: request } : {}),
        output: receipt.output,
      } as unknown as AiUiPart,
    })
  }
  return [...deduped.values()]
}

/** 未返回的历史调用不等于审批，也不能因为传输已结束就当作成功。 */
export function aiToolStatus(part: AiUiPart, pendingKind?: PendingToolCall['kind'], active = false): ToolDisplayStatus {
  const tool = part as ToolPartView
  const output = record(tool.output)
  const error = record(output.error)
  if (desktopImageAction(part)) return 'desktop'
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
  return ['approval', 'unknown', 'failed', 'partial', 'denied', 'desktop'].includes(status)
}

export interface DisplayPart {
  key: string
  part: AiUiPart
  pendingKind?: PendingToolCall['kind']
  progress?: string
}

/** 实时的多个 step 和历史的多个 response 使用相同规则，保留原始顺序和引用。 */
export function presentAssistantParts(
  parts: AiUiPart[],
  pending: PendingToolCall[] = [],
  active = false,
  writeReceipts: BusinessWriteReceipt[] = [],
) {
  const pendingById = new Map(pending.map(call => [call.tool_call_id, call]))
  const entries: DisplayPart[] = parts.filter(part => part.type !== 'step-start').map((part, index) => ({
    key: isAiToolPart(part) && 'toolCallId' in part ? `tool:${part.toolCallId}` : `part:${index}`,
    part,
    pendingKind: 'toolCallId' in part ? pendingById.get(String(part.toolCallId))?.kind : undefined,
    progress: 'toolCallId' in part ? pendingById.get(String(part.toolCallId))?.progress : undefined,
  }))
  const seenToolCallIds = new Set(
    entries
      .filter(entry => isAiToolPart(entry.part) && 'toolCallId' in entry.part && typeof entry.part.toolCallId === 'string')
      .map(entry => String((entry.part as ToolPartView).toolCallId)),
  )
  const receiptActions = desktopImageReceiptParts(writeReceipts, seenToolCallIds)
  // 待审批或后台执行时仍保留完整时间线；结束后才派生折叠的过程。
  const live = active || entries.some(entry => entry.pendingKind !== undefined)
  const timeline = [...entries.filter(({ part }) => part.type !== 'text' || part.text.trim()), ...receiptActions]
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
  attention.push(...receiptActions)
  return { live, timeline, process, attention, response, hasActiveTool }
}
