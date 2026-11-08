import { computed, ref, shallowRef } from 'vue'
import { defineStore } from 'pinia'
import { describeAiPageContext, type AiPageContext } from '@/types/aiPageContext'

const PREFERENCE_KEY = 'ai-chat-read-background'

export const useAiPageContextStore = defineStore('aiPageContext', () => {
  const enabled = ref(localStorage.getItem(PREFERENCE_KEY) !== 'off')
  const sources = shallowRef(new Map<symbol, { priority: number; context: AiPageContext }>())
  const current = computed(() => [...sources.value.values()]
    .sort((a, b) => b.priority - a.priority)[0]?.context ?? null)
  const hint = computed(() => [
    describeAiPageContext(current.value),
    enabled.value ? '背景读取已开启' : '背景读取已关闭，发送消息时不会附带页面背景',
  ].join('\n'))

  function toggle(): void {
    enabled.value = !enabled.value
    localStorage.setItem(PREFERENCE_KEY, enabled.value ? 'on' : 'off')
  }

  function setSource(owner: symbol, context: AiPageContext | null, priority = 0): void {
    const next = new Map(sources.value)
    if (context) next.set(owner, { priority, context })
    else next.delete(owner)
    sources.value = next
  }

  function snapshot(): AiPageContext | undefined {
    if (!enabled.value || !current.value) return undefined
    // 在发送瞬间复制，避免等待网络或排队时读取到切换后的页面。
    return JSON.parse(JSON.stringify(current.value)) as AiPageContext
  }

  return { enabled, current, hint, toggle, setSource, snapshot }
})
