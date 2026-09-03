/**
 * 通用 AI presentation 前端边界：reserve / status / observe transport。
 *
 * 契约（docs/aiworkpage.md §4、§5）：
 * - `POST /api/v1/ai-presentations` 预留一次展示（服务端生成 ID，短 TTL），
 *   不启动 Agent、不读取业务数据、不选择能力；
 * - `GET /api/v1/ai-presentations/{presentation_id}` 只返回展示元数据；
 * - observe `Chat` 的 id 是 `presentation_id`，transport reconnect 指向
 *   `GET /api/v1/ai-presentations/{presentation_id}/stream`（官方 Vercel UI
 *   SSE，只观察）；`chat.resumeStream()` 不发送伪造用户消息；204 表示没有
 *   可用流；
 * - 业务结果始终来自原业务 API；presentation 层不提供业务 result endpoint。
 */

import { Chat } from '@ai-sdk/vue'
import { DefaultChatTransport } from 'ai'
import type { UIMessage } from 'ai'
import { apiClient } from './client'
import { asRecord, getString } from './workflow/normalizers'

export const AI_PRESENTATIONS_PATH = '/api/v1/ai-presentations'

/** 前台展示重复触发时的稳定错误码。 */
export const AI_FOREGROUND_RUN_ACTIVE = 'AI_FOREGROUND_RUN_ACTIVE'

export type AiPresentationStatus =
  | 'reserved'
  | 'bound'
  | 'running'
  | 'finalizing'
  | 'completed'
  | 'failed'
  | 'expired'

export const AI_PRESENTATION_TERMINAL_STATUSES: ReadonlySet<string> = new Set([
  'completed',
  'failed',
  'expired',
])

export interface AiPresentationDescriptor {
  presentationId: string
  conversationId: string
  displayTitle: string
  status: AiPresentationStatus
}

export interface AiPresentationError extends Error {
  code?: string
  status?: number
}

export interface PresentationObserveChatOptions {
  initialUserMessage?: string
  onError?: (error: Error) => void
}

function normalizePresentationStatus(value: unknown): AiPresentationStatus {
  const text = String(value || '').trim()
  switch (text) {
    case 'reserved':
    case 'bound':
    case 'running':
    case 'finalizing':
    case 'completed':
    case 'failed':
    case 'expired':
      return text
    default:
      return 'running'
  }
}

export function normalizePresentationDescriptor(raw: unknown): AiPresentationDescriptor {
  const record = asRecord(raw)
  const presentationId = getString(record, ['presentation_id'])
  const conversationId = getString(record, ['conversation_id'])
  if (!presentationId || !conversationId) {
    throw new Error('AI 展示响应缺少 presentation_id / conversation_id。')
  }
  return {
    presentationId,
    conversationId,
    displayTitle: getString(record, ['display_title']),
    status: normalizePresentationStatus(record.status),
  }
}

/** POST reserve：服务端预留展示并立即返回 descriptor（不执行 Agent）。 */
export async function reserveAiPresentation(
  displayTitle: string,
): Promise<AiPresentationDescriptor> {
  const response = await apiClient.post(AI_PRESENTATIONS_PATH, {
    display_title: displayTitle,
  })
  return normalizePresentationDescriptor(response.data)
}

/** GET status：只含展示元数据（含脱敏展示错误），不含业务结果。 */
export async function fetchAiPresentationStatus(
  presentationId: string,
): Promise<AiPresentationDescriptor> {
  const response = await apiClient.get(
    `${AI_PRESENTATIONS_PATH}/${encodeURIComponent(presentationId)}`,
  )
  return normalizePresentationDescriptor(response.data)
}

/** observe fetch：非 2xx/204 时解析后端标准 JSON 错误并附加 code/status。 */
async function observePresentationFetch(
  input: RequestInfo | URL,
  init?: RequestInit,
): Promise<Response> {
  const response = await fetch(input, init)
  if (response.ok || response.status === 204) {
    return response
  }
  let code = 'AI_PRESENTATION_HTTP_ERROR'
  let message = `HTTP ${response.status}`
  try {
    const body = (await response.json()) as { error?: string; error_code?: string }
    if (body?.error_code) code = body.error_code
    if (body?.error) message = body.error
  } catch {
    // 非 JSON 错误体保持默认 HTTP 文案。
  }
  const error = new Error(message) as AiPresentationError
  error.code = code
  error.status = response.status
  throw error
}

/**
 * 创建 presentation observe Chat：`id` 为 presentation_id，transport reconnect
 * 指向 observe 流。该 Chat 永远不调用 sendMessage（展示只读）；status/messages
 * 由官方 SSE chunk 驱动。
 */
export function createPresentationObserveChat(
  presentationId: string,
  options: PresentationObserveChatOptions = {},
): Chat<UIMessage> {
  const initialUserMessage = String(options.initialUserMessage || '').trim()
  return new Chat<UIMessage>({
    id: presentationId,
    messages: initialUserMessage
      ? [{
          id: `${presentationId}:initial-user`,
          role: 'user',
          parts: [{ type: 'text', text: initialUserMessage }],
        }]
      : [],
    transport: new DefaultChatTransport<UIMessage>({
      api: AI_PRESENTATIONS_PATH,
      credentials: 'same-origin',
      fetch: observePresentationFetch,
      // observe Chat 不发送消息；若被误调用，请求体也不携带任何业务字段。
      prepareSendMessagesRequest: () => ({ body: {} }),
    }),
    onError: (error) => {
      options.onError?.(error instanceof Error ? error : new Error(String(error)))
    },
  })
}

export function presentationStreamPath(presentationId: string): string {
  return `${AI_PRESENTATIONS_PATH}/${encodeURIComponent(presentationId)}/stream`
}

export function describePresentationStatus(status: AiPresentationStatus): string {
  switch (status) {
    case 'reserved':
      return '已预留'
    case 'bound':
      return '已绑定'
    case 'running':
      return '运行中'
    case 'finalizing':
      return '收尾中'
    case 'completed':
      return '已完成'
    case 'failed':
      return '失败'
    case 'expired':
      return '已过期'
    default:
      return status
  }
}
