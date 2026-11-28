import type { UIMessage } from 'ai'
import type { AiTurnTiming, AiUiPart, BusinessWriteReceipt, CodeProgressSnapshot, ManualAiAction, PendingToolCall } from '@/types/aiWork'
import { describeAiTool, describeRunCodeAction } from './aiToolLabels'

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
    if (message.role === 'system') continue
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
const FATAL_MODEL_ERROR_CODES = new Set([
  'AI_PROVIDER_PAYMENT_REQUIRED',
  'AI_PROVIDER_AUTH_FAILED',
  'HTTP_401',
  'HTTP_403',
])
const FATAL_MODEL_HTTP_STATUSES = new Set([401, 402, 403])

export interface FatalModelInfraError {
  code: string
  message: string
  statusCode?: number
  useCaseId: string
  useCaseLabel: string
  modelId: string
  modelName: string
  affectedCount: number
}

function asNonEmptyString(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}

export function fatalModelInfraErrorFromOutput(output: unknown): FatalModelInfraError | null {
  const root = record(output)
  if (root.ok !== false) return null
  const error = record(root.error)
  const details = record(error.details)
  const code = asNonEmptyString(error.code)
  const rawStatus = details.status_code
  const parsedStatus = typeof rawStatus === 'number'
    ? rawStatus
    : (typeof rawStatus === 'string' && /^\d+$/.test(rawStatus.trim()) ? Number(rawStatus.trim()) : undefined)
  const statusCode = parsedStatus !== undefined && Number.isFinite(parsedStatus) ? parsedStatus : undefined
  if (!FATAL_MODEL_ERROR_CODES.has(code) && !(statusCode !== undefined && FATAL_MODEL_HTTP_STATUSES.has(statusCode))) {
    return null
  }
  const affectedCount = typeof root._affected_count === 'number' && root._affected_count > 1
    ? Math.floor(root._affected_count)
    : 1
  return {
    code: code || (statusCode === 402 ? 'AI_PROVIDER_PAYMENT_REQUIRED' : `HTTP_${statusCode}`),
    message: asNonEmptyString(error.message),
    ...(statusCode !== undefined ? { statusCode } : {}),
    useCaseId: asNonEmptyString(details.use_case_id),
    useCaseLabel: asNonEmptyString(details.use_case_label),
    modelId: asNonEmptyString(details.model_id),
    modelName: asNonEmptyString(details.model_name),
    affectedCount,
  }
}

export function fatalModelErrorInfo(part: AiUiPart): FatalModelInfraError | null {
  if (!isAiToolPart(part)) return null
  return fatalModelInfraErrorFromOutput((part as ToolPartView).output)
}

function fatalModelErrorDedupeKey(info: FatalModelInfraError, fallbackToolName: string): string {
  const statusOrCode = info.statusCode === 402 || info.code === 'AI_PROVIDER_PAYMENT_REQUIRED'
    ? 'AI_PROVIDER_PAYMENT_REQUIRED'
    : (info.statusCode ? `HTTP_${info.statusCode}` : info.code)
  return `${statusOrCode}|${info.useCaseId}|${info.modelId || info.modelName || fallbackToolName}`
}

function fatalModelReceiptParts(
  receipts: BusinessWriteReceipt[],
  seenToolCallIds: Set<string>,
  existingParts: AiUiPart[],
): DisplayPart[] {
  const existingKeys = new Set<string>()
  for (const part of existingParts) {
    const info = fatalModelErrorInfo(part)
    if (info) existingKeys.add(fatalModelErrorDedupeKey(info, aiToolName(part)))
  }
  const deduped = new Map<string, { receipt: BusinessWriteReceipt; count: number }>()
  for (const receipt of receipts) {
    if (seenToolCallIds.has(receipt.tool_call_id)) continue
    const info = fatalModelInfraErrorFromOutput(receipt.output)
    if (!info) continue
    const key = fatalModelErrorDedupeKey(info, receipt.tool_name)
    if (existingKeys.has(key)) continue
    const current = deduped.get(key)
    if (current) current.count += 1
    else deduped.set(key, { receipt, count: 1 })
  }
  return [...deduped.values()].map(({ receipt, count }) => ({
    key: `fatal-receipt:${receipt.tool_call_id}`,
    part: {
      type: 'dynamic-tool',
      toolName: receipt.tool_name,
      toolCallId: receipt.tool_call_id,
      state: 'output-available',
      output: count > 1 ? { ...receipt.output, _affected_count: count } : receipt.output,
    } as unknown as AiUiPart,
  }))
}

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

/** 判断单个回执是否包含未确认的写副作用结果。 */
function outputHasUnknownOutcome(output: unknown): boolean {
  const root = record(output)
  const error = record(root.error)
  return root.outcome_unknown === true
    || record(root.details).outcome_unknown === true
    || record(error.details).outcome_unknown === true
    || root.status === 'unknown'
}

/** 提取某次 run_code 对应的嵌套业务写回执。 */
export function nestedWriteReceiptsForCall(
  toolCallId: string | undefined,
  writeReceipts: BusinessWriteReceipt[] = [],
): BusinessWriteReceipt[] {
  if (!toolCallId || !writeReceipts.length) return []
  const prefix = `${toolCallId}__`
  return writeReceipts.filter(receipt => receipt.tool_call_id.startsWith(prefix))
}

function isRunCodeRuntimeDiagnosticError(part: AiUiPart): boolean {
  if (aiToolName(part) !== 'run_code') return false
  const tool = part as ToolPartView
  if (tool.state !== 'output-error') return false
  const output = record(tool.output)
  if (outputHasUnknownOutcome(output) || output.ok === false || output.status === 'failed') return false
  return /^Runtime error:\r?\n/.test(tool.errorText || '')
}

/** 未返回的历史调用不等于审批，也不能因为传输已结束就当作成功。 */
export function aiToolStatus(part: AiUiPart, pendingKind?: PendingToolCall['kind'], active = false): ToolDisplayStatus {
  const tool = part as ToolPartView
  const output = record(tool.output)
  if (desktopImageAction(part)) return 'desktop'
  if (outputHasUnknownOutcome(output)) return 'unknown'
  if (tool.state === 'output-denied') return 'denied'
  if (output.ok === false || output.status === 'failed') return 'failed'
  if (tool.state === 'output-error') {
    // Code Mode 原生 ModelRetry 将执行前诊断放在 errorText，没有结构化错误类别。
    // 只识别其固定前缀；运行时错误是否已由后续步骤恢复由 presentAssistantParts 结合上下文判定。
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
  statusOverride?: ToolDisplayStatus
}

export function displayToolStatus(entry: DisplayPart, active = false): ToolDisplayStatus {
  return entry.statusOverride ?? aiToolStatus(entry.part, entry.pendingKind, active)
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
  const fatalReceiptAlerts = fatalModelReceiptParts(
    writeReceipts,
    seenToolCallIds,
    entries.map(entry => entry.part),
  )
  // 待审批或后台执行时仍保留完整时间线；结束后才派生折叠的过程。
  const live = active || entries.some(entry => entry.pendingKind !== undefined)
  // 先计算每个工具的基础状态，识别嵌套回执中的未知结果，再判断是否已有后续步骤恢复中间脚本运行时异常。
  const baseStatuses = entries.map(entry => (
    isAiToolPart(entry.part) ? aiToolStatus(entry.part, entry.pendingKind, live) : null
  ))
  const hasNestedUnknown = entries.map(entry => {
    if (!isAiToolPart(entry.part) || aiToolName(entry.part) !== 'run_code') return false
    const callId = (entry.part as ToolPartView).toolCallId
    return nestedWriteReceiptsForCall(callId, writeReceipts).some(receipt => outputHasUnknownOutcome(receipt.output))
  })
  const progressedToolIndices: number[] = []
  baseStatuses.forEach((status, index) => {
    if (!status || hasNestedUnknown[index]) return
    if (['completed', 'submitted', 'preparing', 'waiting', 'running'].includes(status)) {
      progressedToolIndices.push(index)
    }
  })
  const lastProgressedToolIndex = progressedToolIndices.at(-1) ?? -1
  entries.forEach((entry, index) => {
    if (!isAiToolPart(entry.part)) return
    if (hasNestedUnknown[index]) {
      entry.statusOverride = 'unknown'
      return
    }
    // 中途 run_code 运行时异常（非未知副作用）且后续步骤已继续执行/完成时，归为内部重试调整，避免完成态残留误导性红框。
    if (
      baseStatuses[index] === 'failed'
      && isRunCodeRuntimeDiagnosticError(entry.part)
      && index < lastProgressedToolIndex
    ) {
      entry.statusOverride = 'script_error'
    }
  })
  const timeline = [
    ...entries.filter(({ part }) => part.type !== 'text' || part.text.trim()),
    ...receiptActions,
    ...fatalReceiptAlerts,
  ]
  // 不按文本内容猜测“最终答案”，也不丢弃中途问题、附件或来源。
  let lastTool = -1
  entries.forEach((entry, index) => { if (isAiToolPart(entry.part)) lastTool = index })
  const process: DisplayPart[] = []
  const attention: DisplayPart[] = []
  const response: DisplayPart[] = []
  let hasActiveTool = false
  for (const [index, entry] of entries.entries()) {
    const { part } = entry
    if (isAiToolPart(part)) {
      const status = displayToolStatus(entry, live)
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
  attention.push(...receiptActions, ...fatalReceiptAlerts)
  return { live, timeline, process, attention, response, hasActiveTool }
}

/** 返回单个工具条目的可读业务名称（屏蔽 run_code 技术外壳）。 */
export function describeToolPartLabel(
  part: AiUiPart,
  codeProgress?: CodeProgressSnapshot,
  writeReceipts?: BusinessWriteReceipt[],
): string {
  const rawName = aiToolName(part)
  const tool = part as ToolPartView
  if (rawName === 'run_code') {
    return describeRunCodeAction(tool.input, codeProgress, writeReceipts, tool.toolCallId)
  }
  return describeAiTool(rawName)
}

/** 构造完成态折叠过程头的语义摘要（参考 Claude.ai / Codex）。 */
export function summarizeCompletedProcess(
  entries: DisplayPart[],
  writeReceipts: BusinessWriteReceipt[] = [],
  duration = '',
): string {
  const tools = entries.filter(entry => isAiToolPart(entry.part))
  if (!tools.length) {
    return duration ? `用时 ${duration}` : '处理过程'
  }
  const meaningfulTools = tools.filter(entry => displayToolStatus(entry, false) !== 'script_error')
  const counted = meaningfulTools.length || tools.length
  const genericLabels = new Set(['核对与整理数据', '处理业务', '处理操作'])
  const specificLabels: string[] = []
  const fallbackLabels: string[] = []
  for (const entry of (meaningfulTools.length ? meaningfulTools : tools)) {
    const label = describeToolPartLabel(entry.part, undefined, writeReceipts)
    if (!label) continue
    if (genericLabels.has(label)) {
      if (!fallbackLabels.includes(label)) fallbackLabels.push(label)
    } else if (!specificLabels.includes(label)) {
      specificLabels.push(label)
    }
  }
  const labels = specificLabels.length ? specificLabels : fallbackLabels
  const shown = labels.slice(0, 2).join('、')
  const parts = [`已处理 ${counted} 步`]
  if (shown) parts.push(shown)
  if (duration) parts.push(duration)
  return parts.join(' · ')
}

/**
 * 按“旁白 + 触发的工具”逻辑阶段拆分运行中时间线，
 * 避免长任务折叠时把一句过渡说明与它触发的工具硬拆开。
 */
export function splitLiveTimeline(entries: DisplayPart[]): { earlier: DisplayPart[]; recent: DisplayPart[]; earlierToolCount: number } {
  const keepTail = 3
  if (entries.length <= keepTail + 2) {
    return { earlier: [], recent: entries, earlierToolCount: 0 }
  }
  // 将连续的“旁白文本 + 紧随其后的工具”划为同一阶段，其余条目按自然边界分组。
  const stages: DisplayPart[][] = []
  let current: DisplayPart[] = []
  let currentHasTool = false
  for (const entry of entries) {
    const isTool = isAiToolPart(entry.part)
    if (!isTool && current.length && currentHasTool) {
      stages.push(current)
      current = [entry]
      currentHasTool = false
      continue
    }
    if (isTool && current.length && !currentHasTool) {
      current.push(entry)
      currentHasTool = true
      continue
    }
    if (isTool && currentHasTool) {
      stages.push(current)
      current = [entry]
      currentHasTool = true
      continue
    }
    current.push(entry)
  }
  if (current.length) stages.push(current)

  const maxCutoff = entries.length - keepTail
  const earlier: DisplayPart[] = []
  let earlierToolCount = 0
  for (const stage of stages) {
    if (earlier.length + stage.length > maxCutoff) break
    const foldable = stage.every(entry => {
      if (!isAiToolPart(entry.part)) return true
      if (aiToolName(entry.part) === 'request_user_input' || fatalModelErrorInfo(entry.part)) return false
      const st = displayToolStatus(entry, true)
      return st === 'completed' || st === 'script_error'
    })
    if (!foldable) break
    earlier.push(...stage)
    earlierToolCount += stage.filter(entry => isAiToolPart(entry.part)).length
  }

  if (earlier.length < 3 && earlierToolCount < 3) {
    return { earlier: [], recent: entries, earlierToolCount: 0 }
  }
  return {
    earlier,
    recent: entries.slice(earlier.length),
    earlierToolCount: earlierToolCount || earlier.length,
  }
}
