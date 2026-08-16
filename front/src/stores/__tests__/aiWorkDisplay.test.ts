import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { Chat } from '@ai-sdk/vue'
import type { UIMessage } from 'ai'
import { useAiWorkDisplayStore } from '@/stores/aiWorkDisplay'
import type { AiPresentationDescriptor } from '@/api/aiPresentations'

function fakeChat(messages: UIMessage[] = []): Chat<UIMessage> {
  return new Chat<UIMessage>({ id: 'presentation_x', messages })
}

function descriptor(presentationId = 'presentation_x'): AiPresentationDescriptor {
  return {
    presentationId,
    conversationId: `conversation_${presentationId}`,
    displayTitle: 'AI 匹配类目',
    status: 'reserved',
  }
}

describe('aiWorkDisplay store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('默认展示 global-chat，没有前台 presentation', () => {
    const store = useAiWorkDisplayStore()
    expect(store.displayMode).toBe('global-chat')
    expect(store.hasForegroundPresentation).toBe(false)
    expect(store.presentationMessages).toEqual([])
  })

  it('attach 后进入 presentation 展示模式并携带展示描述', () => {
    const store = useAiWorkDisplayStore()
    store.attachForegroundPresentation(descriptor(), fakeChat())

    expect(store.displayMode).toBe('presentation')
    expect(store.foregroundPresentation?.presentationId).toBe('presentation_x')
    expect(store.foregroundPresentation?.displayTitle).toBe('AI 匹配类目')
    expect(store.presentationStatusText).toBe('已预留')
  })

  it('同一时间只允许一个前台 presentation：第二次 attach 被明确拒绝', () => {
    const store = useAiWorkDisplayStore()
    store.attachForegroundPresentation(descriptor(), fakeChat())

    expect(() => store.attachForegroundPresentation(descriptor('presentation_y'), fakeChat()))
      .toThrow('已有前台 AI 任务运行')
    expect(store.foregroundPresentation?.presentationId).toBe('presentation_x')
  })

  it('beginForegroundStart 同步占用：reserve POST 前即阻止并发第二次触发', () => {
    const store = useAiWorkDisplayStore()

    store.beginForegroundStart()
    expect(store.foregroundStartPending).toBe(true)
    expect(store.foregroundOccupied).toBe(true)
    // 尚未 attach（reserve POST 进行中）：第二次同步触发即被拒绝。
    expect(() => store.beginForegroundStart()).toThrow('已有前台 AI 任务运行')
    expect(store.hasForegroundPresentation).toBe(false)

    // attach 后 pending 转为真实 presentation 占用。
    store.attachForegroundPresentation(descriptor(), fakeChat())
    expect(store.foregroundStartPending).toBe(false)
    expect(store.foregroundOccupied).toBe(true)
    expect(() => store.beginForegroundStart()).toThrow('已有前台 AI 任务运行')
  })

  it('活动 presentation 存在时 beginForegroundStart 被拒绝', () => {
    const store = useAiWorkDisplayStore()
    store.attachForegroundPresentation(descriptor(), fakeChat())

    expect(() => store.beginForegroundStart()).toThrow('已有前台 AI 任务运行')
    expect(store.foregroundStartPending).toBe(false)
  })

  it('abortForegroundStart 释放启动期占用，允许重新触发', () => {
    const store = useAiWorkDisplayStore()

    store.beginForegroundStart()
    store.abortForegroundStart()
    expect(store.foregroundOccupied).toBe(false)

    // 释放后下一次触发可以正常占用。
    store.beginForegroundStart()
    expect(store.foregroundStartPending).toBe(true)
  })

  it('状态与错误更新不会覆盖 observe Chat 引用', () => {
    const store = useAiWorkDisplayStore()
    const chat = fakeChat()
    store.attachForegroundPresentation(descriptor(), chat)

    store.updateForegroundStatus('running')
    expect(store.foregroundPresentation?.chat).toBe(chat)
    expect(store.presentationStatusText).toBe('运行中')

    store.setForegroundError(new Error('AI_AGENT_RUN_FAILED'))
    expect(store.foregroundPresentation?.chat).toBe(chat)
    expect(store.foregroundPresentation?.error?.message).toBe('AI_AGENT_RUN_FAILED')
  })

  it('finish 恢复 global-chat，保留短暂提示并递增版本', () => {
    const store = useAiWorkDisplayStore()
    store.attachForegroundPresentation(descriptor(), fakeChat())

    store.finishForegroundPresentation({ kind: 'success', text: '类目匹配完成' })

    expect(store.displayMode).toBe('global-chat')
    expect(store.foregroundPresentation).toBeNull()
    expect(store.terminalNotice?.text).toBe('类目匹配完成')
    expect(store.presentationVersion).toBe(1)

    store.clearTerminalNotice()
    expect(store.terminalNotice).toBeNull()
  })

  it('presentationMessages 是 observe Chat 消息的渲染副本', () => {
    const store = useAiWorkDisplayStore()
    const message: UIMessage = {
      id: 'm1',
      role: 'assistant',
      parts: [{ type: 'text', text: '候选：Ventiladores' }],
    }
    const chat = fakeChat([message])
    store.attachForegroundPresentation(descriptor(), chat)

    expect(store.presentationMessages).toEqual([message])
    expect(store.presentationMessages[0]).not.toBe(message)
  })
})
