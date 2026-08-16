/**
 * AiWork 展示协调层：前台 presentation 与全局聊天的显示切换。
 *
 * 约束（docs/aiworkpage.md §1、§3、§4）：
 * - `AiChatStore` 仍是全局聊天唯一 owner；本 store 不复制其消息、不拥有其输入框；
 * - 同一标签页同一时间最多一个用户直接触发的前台 presentation；第二个触发被明确拒绝；
 * - 业务终态后才恢复 global-chat，不得永久卡在 presentation 模式；
 * - 全局聊天的消息、输入与 SSE 状态在接管期间完全不变。
 */

import { computed, ref, shallowRef } from 'vue'
import { defineStore } from 'pinia'
import type { Chat } from '@ai-sdk/vue'
import type { ChatStatus, UIMessage } from 'ai'
import {
  AI_FOREGROUND_RUN_ACTIVE,
  AI_PRESENTATION_TERMINAL_STATUSES,
  describePresentationStatus,
  type AiPresentationDescriptor,
  type AiPresentationError,
  type AiPresentationStatus,
} from '@/api/aiPresentations'

export interface ForegroundPresentation extends AiPresentationDescriptor {
  chat: Chat<UIMessage>
  error: Error | null
}

export interface ForegroundTerminalNotice {
  kind: 'success' | 'failure'
  text: string
}

export const useAiWorkDisplayStore = defineStore('aiWorkDisplay', () => {
  const foregroundPresentation = shallowRef<ForegroundPresentation | null>(null)
  // 启动期同步占用：reserve POST 发出前即置位，保证“检查 → reserve → attach”原子，
  // 并发触发不能都通过检查、启动两个前台 presentation。
  const foregroundStartPending = ref(false)
  const terminalNotice = ref<ForegroundTerminalNotice | null>(null)
  // 每次前台 presentation terminal 后递增，供 AiWork 页面刷新列表/历史。
  const presentationVersion = ref(0)

  const displayMode = computed<'presentation' | 'global-chat'>(() => (
    foregroundPresentation.value ? 'presentation' : 'global-chat'
  ))
  const hasForegroundPresentation = computed(() => foregroundPresentation.value !== null)
  /** 前台占用 = 已有活动 presentation，或一个启动中的 presentation（reserve POST 进行中）。 */
  const foregroundOccupied = computed(() => (
    foregroundPresentation.value !== null || foregroundStartPending.value
  ))
  const foregroundIsTerminal = computed(() => (
    foregroundPresentation.value !== null
    && AI_PRESENTATION_TERMINAL_STATUSES.has(foregroundPresentation.value.status)
  ))
  const presentationStatusText = computed(() => (
    foregroundPresentation.value
      ? describePresentationStatus(foregroundPresentation.value.status)
      : ''
  ))
  const presentationChatStatus = computed<ChatStatus>(() => (
    foregroundPresentation.value?.chat.status ?? 'ready'
  ))
  const presentationBusy = computed(() => (
    presentationChatStatus.value === 'submitted' || presentationChatStatus.value === 'streaming'
  ))

  // 与 AiChatStore.messages 相同的渲染桥：AI SDK 对消息数组做索引级原地更新，
  // 这里建立索引级依赖并返回结构化副本；事实源仍是 chat.messages 与服务端历史。
  const presentationMessages = computed<UIMessage[]>(() => {
    const current = foregroundPresentation.value?.chat.messages
    if (!current || current.length === 0) return []
    return JSON.parse(JSON.stringify(current)) as UIMessage[]
  })

  /**
   * 同步占用前台启动窗口：检查与占用在同一步完成，调用方必须在 reserve POST
   * 之前调用。已有活动 presentation 或另一个启动中时明确拒绝（不隐式覆盖）。
   */
  function beginForegroundStart(): void {
    if (foregroundOccupied.value) {
      const error = new Error('已有前台 AI 任务运行，请等待其完成后再试。') as AiPresentationError
      error.name = 'AiForegroundRunActiveError'
      error.code = AI_FOREGROUND_RUN_ACTIVE
      throw error
    }
    foregroundStartPending.value = true
  }

  /** reserve 失败时释放同步占用，允许下一次触发重新尝试。 */
  function abortForegroundStart(): void {
    foregroundStartPending.value = false
  }

  /** 注册前台 presentation；已有前台 presentation 时明确拒绝（不隐式覆盖）。 */
  function attachForegroundPresentation(
    descriptor: AiPresentationDescriptor,
    chat: Chat<UIMessage>,
  ): ForegroundPresentation {
    if (foregroundPresentation.value) {
      const error = new Error('已有前台 AI 任务运行，请等待其完成后再试。')
      error.name = 'AiForegroundRunActiveError'
      throw error
    }
    foregroundStartPending.value = false
    terminalNotice.value = null
    const presentation: ForegroundPresentation = { ...descriptor, chat, error: null }
    foregroundPresentation.value = presentation
    return presentation
  }

  function patchForeground(
    patch: Partial<Omit<ForegroundPresentation, 'presentationId'>>,
  ): void {
    const current = foregroundPresentation.value
    if (!current) return
    foregroundPresentation.value = { ...current, ...patch }
  }

  function updateForegroundStatus(status: AiPresentationStatus): void {
    patchForeground({ status })
  }

  function setForegroundError(error: Error | null): void {
    patchForeground({ error })
  }

  /** terminal 收尾：记录短暂提示、递增版本并恢复 global-chat 展示。 */
  function finishForegroundPresentation(notice: ForegroundTerminalNotice | null): void {
    foregroundStartPending.value = false
    if (!foregroundPresentation.value) return
    foregroundPresentation.value = null
    terminalNotice.value = notice
    presentationVersion.value += 1
  }

  function clearTerminalNotice(): void {
    terminalNotice.value = null
  }

  return {
    foregroundPresentation,
    foregroundStartPending,
    terminalNotice,
    presentationVersion,
    displayMode,
    hasForegroundPresentation,
    foregroundOccupied,
    foregroundIsTerminal,
    presentationStatusText,
    presentationChatStatus,
    presentationBusy,
    presentationMessages,
    beginForegroundStart,
    abortForegroundStart,
    attachForegroundPresentation,
    updateForegroundStatus,
    setForegroundError,
    finishForegroundPresentation,
    clearTerminalNotice,
  }
})
