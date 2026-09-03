/**
 * 通用前台 presentation wrapper（docs/aiworkpage.md §4、§6）。
 *
 * 业务 action 仍 `await` 原业务接口的类型化 Promise；presentation 层只负责
 * 浮窗/AiWork 的只读实时展示：
 *
 * 1. `beginForegroundStart()` 同步原子占用前台；
 * 2. reserve 取得服务端 presentation/conversation ID；
 * 3. 创建只读 observe Chat 并 attach；
 * 4. 展示流（`resumeStream()`）与业务请求**并发**启动，不等待一个完成才开始另一个；
 * 5. 业务 response 是唯一结果事实：SSE 失败不得把成功改写为失败，SSE 正常
 *    也不得把失败改写为成功；
 * 6. 业务终态后有界等待展示流收尾，再 `finishForegroundPresentation()` 恢复
 *    global-chat（其 Chat、messages、input、status、SSE 全程不变）。
 *
 * 无 Agent 路径（rules-only、提前返回）同样成立：后端 request boundary 在
 * handler 结束时关闭未产生 Agent 的展示流，wrapper 用业务 response 收尾。
 */

import {
  createPresentationObserveChat,
  reserveAiPresentation,
  type AiPresentationDescriptor,
  type AiPresentationError,
} from '@/api/aiPresentations'
import { useAiWorkDisplayStore } from '@/stores/aiWorkDisplay'

/** 业务终态后等待展示流收尾的有界预算；超时直接收尾，不无限占用前台。 */
export const PRESENTATION_STREAM_SETTLE_MS = 5_000

export interface AiForegroundOptions<TResult> {
  displayTitle: string
  initialUserMessage?: string
  successNotice?: (result: TResult) => string
  failureNotice?: (error: unknown) => string
}

/** 展示失败的展示文案：保留原错误 code/status，只改写面向用户的说明。 */
function toDisplayFailure(error: unknown): Error {
  const source = error instanceof Error ? error : new Error(String(error))
  const wrapped = new Error(
    `实时展示连接中断，正在等待业务结果…（${source.message || '未知错误'}）`,
  ) as AiPresentationError
  const sourceWithCode = source as AiPresentationError
  wrapped.code = sourceWithCode.code
  wrapped.status = sourceWithCode.status
  return wrapped
}

export async function withAiForeground<TResult>(
  options: AiForegroundOptions<TResult>,
  operation: (context: { presentationId: string }) => Promise<TResult>,
): Promise<TResult> {
  const display = useAiWorkDisplayStore()
  // 前台占用必须原子：同步检查并占用 starting 状态，再发起 reserve POST。
  // 若先 await reserve 再占用，并发触发会都通过检查、预留两个 presentation。
  display.beginForegroundStart()
  let descriptor: AiPresentationDescriptor
  let chat: ReturnType<typeof createPresentationObserveChat>
  let streamError: Error | null = null
  try {
    descriptor = await reserveAiPresentation(options.displayTitle)
    chat = createPresentationObserveChat(descriptor.presentationId, {
      initialUserMessage: options.initialUserMessage,
      onError: (error) => {
        streamError ??= error
      },
    })
    display.attachForegroundPresentation(descriptor, chat)
  } catch (error) {
    // reserve/attach 失败：释放同步占用，允许后续触发重新尝试。
    display.abortForegroundStart()
    throw error
  }
  display.updateForegroundStatus('running')

  // 展示链：只读消费官方 SSE，与业务请求并发。它的失败不是业务失败——
  // 只记录降级提示，不在这里放弃，也不改写业务结果。
  const streamSettled = (async () => {
    try {
      await chat.resumeStream()
      if (streamError) throw streamError
      // resumeStream 可能以 error 状态静默结束（SDK 不抛出也不触发 onError 的变体）。
      if (chat.status === 'error' && chat.error) throw chat.error
    } catch (error) {
      const normalized = error instanceof Error ? error : new Error(String(error))
      streamError ??= normalized
      // 面向用户的降级文案包装后再写入展示；原始错误只留在内部。
      display.setForegroundError(toDisplayFailure(normalized))
    }
  })()

  let businessFailed = false
  let businessError: unknown
  let result: TResult | undefined
  try {
    result = await operation({ presentationId: descriptor.presentationId })
  } catch (error) {
    businessFailed = true
    businessError = error
  }

  // 业务终态后展示流应很快关闭（后端边界已收尾 chunk 缓冲）；有界等待，
  // 超时不阻塞前台恢复。
  let settleTimer: ReturnType<typeof setTimeout> | undefined
  await Promise.race([
    streamSettled,
    new Promise<void>((resolve) => {
      settleTimer = setTimeout(resolve, PRESENTATION_STREAM_SETTLE_MS)
    }),
  ]).finally(() => {
    if (settleTimer !== undefined) clearTimeout(settleTimer)
  })

  if (!businessFailed) {
    display.updateForegroundStatus('completed')
    display.finishForegroundPresentation({
      kind: 'success',
      text: options.successNotice?.(result as TResult) ?? `${options.displayTitle}完成`,
    })
    return result as TResult
  }

  const normalized = businessError instanceof Error
    ? businessError
    : new Error(String(businessError))
  display.setForegroundError(normalized)
  display.updateForegroundStatus('failed')
  display.finishForegroundPresentation({
    kind: 'failure',
    text: options.failureNotice?.(businessError)
      || normalized.message
      || `${options.displayTitle}失败`,
  })
  throw normalized
}
