<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import {
  fetchAiWorkConversation,
  fetchAiWorkConversations,
  waitForAiWorkEvents,
} from '@/api/aiWork'
import type { AiWorkConversationSummary, AiWorkEvent } from '@/types/aiWork'
import type { GlobalTaskStatus } from '@/types/globalTasks'
import { formatAiWorkError } from '@/utils/aiWorkError'

const route = useRoute()
const panelVisible = ref(false)
const loading = ref(false)
const error = ref('')
const latestConversation = ref<AiWorkConversationSummary | null>(null)
const latestEvents = ref<AiWorkEvent[]>([])
const floatingElement = ref<HTMLElement | null>(null)
const outputElement = ref<HTMLElement | null>(null)
const pointerWithin = ref(false)
const focusWithin = ref(false)
let pollGeneration = 0
let pollAbortController: AbortController | null = null

const shouldRender = computed(() => route.name !== 'AiWork')
const isGlobalConversation = computed(() => (
  latestConversation.value?.use_case_id === 'global.agent.chat'
))
const lastSeq = computed(() =>
  latestEvents.value.reduce((highest, event) => Math.max(highest, event.seq || 0), 0),
)
const assistantOutput = computed(() =>
  latestEvents.value
    .filter((event) => event.type === 'TEXT_MESSAGE_CONTENT')
    .map((event) => event.delta || '')
    .join(''),
)
const reasoningOutput = computed(() => {
  let output = ''
  for (const event of latestEvents.value) {
    if (event.type === 'REASONING_MESSAGE_START' && output) output += '\n\n'
    if (event.type === 'REASONING_MESSAGE_CONTENT') output += event.delta || ''
  }
  return output
})
const reasoningStarted = computed(() => latestEvents.value.some(
  (event) => event.type === 'REASONING_MESSAGE_START',
))
const reasoningEnded = computed(() => {
  const start = [...latestEvents.value].reverse().find(
    (event) => event.type === 'REASONING_MESSAGE_START',
  )
  const end = [...latestEvents.value].reverse().find(
    (event) => event.type === 'REASONING_MESSAGE_END',
  )
  return Boolean(start && end && end.seq > start.seq)
})
const runError = computed(() =>
  [...latestEvents.value].reverse().find((event) => event.type === 'RUN_ERROR'),
)
const businessResult = computed(() => {
  const event = [...latestEvents.value]
    .reverse()
    .find((item) => item.type === 'CUSTOM' && item.name === 'business.result')
  if (!event) return undefined
  const value = event.value
  if (value && typeof value === 'object' && 'parsed' in value) {
    return (value as { parsed?: unknown }).parsed
  }
  return value
})
const globalAssistantMessage = computed(() => {
  const event = [...latestEvents.value].reverse().find((item) => (
    item.type === 'CUSTOM' && item.name === 'global.assistant_message'
  ))
  return String(asRecord(event?.value)?.message || '').trim()
})
const globalTaskProjection = computed(() => {
  const event = [...latestEvents.value].reverse().find((item) => (
    item.type === 'CUSTOM' && item.name === 'global.task_state'
  ))
  return asRecord(event?.value)
})
const liveStatus = computed<AiWorkConversationSummary['status']>(() => {
  if (latestEvents.value.some((event) => event.type === 'RUN_ERROR')) return 'failed'
  if (latestEvents.value.some((event) => event.type === 'RUN_FINISHED')) return 'completed'
  if (latestEvents.value.some((event) => event.type === 'RUN_DEFERRED')) return 'waiting_approval'
  return latestConversation.value?.status || 'running'
})
type DisplayStatus = AiWorkConversationSummary['status'] | GlobalTaskStatus

const displayStatus = computed<DisplayStatus>(() => {
  if (!isGlobalConversation.value) return liveStatus.value
  const projected = String(globalTaskProjection.value?.status || '') as GlobalTaskStatus
  return projected || latestConversation.value?.latest_task_status || liveStatus.value
})
const displayTerminal = computed(() => [
  'waiting_approval',
  'needs_input',
  'waiting_publish_confirmation',
  'completed',
  'failed',
  'interrupted',
  'cancelled',
].includes(displayStatus.value))
const stopPolling = computed(() => {
  if (isGlobalConversation.value) {
    return ['waiting_approval', 'completed', 'failed', 'interrupted'].includes(
      latestConversation.value?.status || 'running',
    )
  }
  return displayTerminal.value
})
const progressText = computed(() => {
  if (isGlobalConversation.value) {
    if (globalAssistantMessage.value) return globalAssistantMessage.value
    const summary = String(globalTaskProjection.value?.summary || '').trim()
    if (summary) return summary
    return globalTaskFallback(displayStatus.value)
  }
  if (runError.value) return formatAiWorkError(runError.value)
  if (liveStatus.value === 'waiting_approval') return '工具调用正在等待人工审批。'
  if (assistantOutput.value) return assistantOutput.value
  if (businessResult.value !== undefined) return pretty(businessResult.value)
  if (reasoningOutput.value) return reasoningOutput.value
  if (reasoningStarted.value) return 'Provider 已进入推理阶段，正在等待推理内容……'
  if (latestEvents.value.some((event) => (
    event.type === 'CUSTOM'
    && (
      event.name === 'provider.request'
      || event.name === 'capability_probe.request'
      || event.name === 'agent.request'
    )
  ))) {
    return '请求已发送，正在等待 Provider 返回……'
  }
  return loading.value ? '正在读取最新对话……' : '正在准备 Provider 请求……'
})
const liveStageText = computed(() => {
  if (isGlobalConversation.value) return globalTaskStage(displayStatus.value)
  if (displayTerminal.value) return '最终结果'
  if (assistantOutput.value) return '正在生成结果'
  if (reasoningStarted.value && !reasoningEnded.value) return '正在推理'
  if (reasoningStarted.value) return '正在整理推理结果'
  if (latestEvents.value.some((event) => (
    event.type === 'CUSTOM'
    && ['provider.request', 'capability_probe.request', 'agent.request'].includes(String(event.name || ''))
  ))) return '等待 Provider 响应'
  return '正在准备请求'
})

function pretty(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' ? value as Record<string, unknown> : null
}

function statusText(status: DisplayStatus): string {
  return {
    planning: '正在规划',
    running: '进行中',
    needs_input: '等待补充资料',
    waiting_publish_confirmation: '等待发布确认',
    waiting_publish_result: '等待平台结果',
    waiting_approval: '等待审批',
    completed: '已完成',
    failed: '失败',
    interrupted: '已中断',
    cancelled: '已取消',
  }[status]
}

function statusClass(status: DisplayStatus): string {
  if (['planning', 'running', 'waiting_publish_result'].includes(status)) {
    return 'bg-sky-100 text-sky-700 dark:bg-sky-500/15 dark:text-sky-200'
  }
  if (['waiting_approval', 'needs_input', 'waiting_publish_confirmation'].includes(status)) {
    return 'bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-200'
  }
  if (status === 'completed') return 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-200'
  return 'bg-rose-100 text-rose-700 dark:bg-rose-500/15 dark:text-rose-200'
}

function globalTaskStage(status: DisplayStatus): string {
  return {
    planning: '正在规划',
    running: '正在执行',
    needs_input: '等待补充资料',
    waiting_publish_confirmation: '等待发布确认',
    waiting_publish_result: '等待平台结果',
    completed: '最近任务已完成',
    failed: '最近任务失败',
    cancelled: '最近任务已取消',
    waiting_approval: '等待审批',
    interrupted: '任务已中断',
  }[status]
}

function globalTaskFallback(status: DisplayStatus): string {
  return {
    planning: '全局 Agent 正在生成执行计划。',
    running: '全局 Agent 正在执行当前任务。',
    needs_input: '当前任务需要你补充资料，请前往 AI Work 继续。',
    waiting_publish_confirmation: '发布前检查已完成，请前往 AI Work 确认。',
    waiting_publish_result: '发布已提交，正在等待平台真实结果。',
    completed: '最近一次任务已完成。',
    failed: '最近一次任务执行失败，请前往 AI Work 查看详情。',
    cancelled: '最近一次任务已取消。',
    waiting_approval: '当前任务正在等待审批。',
    interrupted: '最近一次任务已中断。',
  }[status]
}

function progressDotClass(status: DisplayStatus): string {
  if (['planning', 'running', 'waiting_publish_result'].includes(status)) {
    return 'animate-pulse bg-sky-500'
  }
  if (status === 'completed') return 'bg-emerald-500'
  if (['needs_input', 'waiting_publish_confirmation', 'waiting_approval'].includes(status)) {
    return 'bg-amber-500'
  }
  return 'bg-rose-500'
}

function formatTime(value: string): string {
  if (!value) return ''
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

function conversationTitle(conversation: AiWorkConversationSummary): string {
  if (conversation.use_case_id === 'global.agent.chat') return '全局 Agent 对话'
  if (conversation.use_case_id === 'config.ai_model_probe') {
    return `能力探测 · ${conversation.capability || '未知能力'}`
  }
  return conversation.use_case_id || conversation.capability
}

async function pollConversation(conversationId: string, generation: number) {
  const abortController = new AbortController()
  pollAbortController?.abort()
  pollAbortController = abortController
  try {
    while (
      generation === pollGeneration
      && panelVisible.value
      && latestConversation.value?.conversation_id === conversationId
      && !stopPolling.value
    ) {
      try {
        const events = await waitForAiWorkEvents(
          conversationId,
          lastSeq.value,
          5_000,
          abortController.signal,
        )
        if (
          generation !== pollGeneration
          || !panelVisible.value
          || latestConversation.value?.conversation_id !== conversationId
        ) return
        if (events.length) {
          const known = new Set(latestEvents.value.map((event) => event.seq))
          latestEvents.value.push(...events.filter((event) => !known.has(event.seq)))
        }
      } catch (cause) {
        if (abortController.signal.aborted || generation !== pollGeneration || !panelVisible.value) return
        error.value = cause instanceof Error ? cause.message : String(cause)
        return
      }
    }
  } finally {
    if (pollAbortController === abortController) pollAbortController = null
  }
}

async function openPanel() {
  if (panelVisible.value) return
  panelVisible.value = true
  const generation = ++pollGeneration
  loading.value = true
  error.value = ''
  try {
    const response = await fetchAiWorkConversations(1)
    if (generation !== pollGeneration || !panelVisible.value) return
    latestConversation.value = response.conversations?.[0] || null
    latestEvents.value = []
    if (!latestConversation.value) return
    const conversationId = latestConversation.value.conversation_id
    const detail = await fetchAiWorkConversation(conversationId)
    if (generation !== pollGeneration || !panelVisible.value) return
    latestConversation.value = detail.conversation || latestConversation.value
    latestEvents.value = detail.events || []
    void pollConversation(conversationId, generation)
  } catch (cause) {
    if (generation !== pollGeneration || !panelVisible.value) return
    error.value = cause instanceof Error ? cause.message : String(cause)
  } finally {
    if (generation === pollGeneration) loading.value = false
  }
}

function closePanel() {
  panelVisible.value = false
  pollGeneration += 1
  pollAbortController?.abort()
  pollAbortController = null
}

function handleMouseEnter() {
  pointerWithin.value = true
  void openPanel()
}

function handleMouseLeave() {
  pointerWithin.value = false
  if (!focusWithin.value) closePanel()
}

function handleFocusIn() {
  focusWithin.value = true
  void openPanel()
}

function handleFocusOut(event: FocusEvent) {
  const nextTarget = event.relatedTarget as Node | null
  if (nextTarget && floatingElement.value?.contains(nextTarget)) return
  focusWithin.value = false
  if (!pointerWithin.value) closePanel()
}

function handleEscape() {
  closePanel()
}

watch(shouldRender, (visible) => {
  if (!visible) closePanel()
})

watch([panelVisible, progressText], async ([visible]) => {
  if (!visible) return
  await nextTick()
  const element = outputElement.value
  if (element) element.scrollTop = element.scrollHeight
}, { flush: 'post' })

onBeforeUnmount(closePanel)
</script>

<template>
  <div
    v-if="shouldRender"
    ref="floatingElement"
    data-testid="ai-work-floating"
    class="fixed bottom-5 right-5 z-[70] flex flex-col items-end gap-3"
    @mouseenter="handleMouseEnter"
    @mouseleave="handleMouseLeave"
    @focusin="handleFocusIn"
    @focusout="handleFocusOut"
    @keydown.esc="handleEscape"
  >
    <section
      v-if="panelVisible"
      id="ai-work-latest-panel"
      role="region"
      aria-label="最新 AI 对话预览"
      data-testid="ai-work-latest"
      class="w-[360px] max-w-[calc(100vw-2.5rem)] overflow-hidden rounded-2xl border border-slate-200 bg-white text-slate-900 shadow-2xl shadow-slate-950/20 dark:border-dark-700 dark:bg-dark-900 dark:text-white"
    >
      <header class="flex items-center justify-between gap-3 border-b border-slate-200 px-4 py-3 dark:border-dark-700">
        <div>
          <p class="text-xs font-black uppercase tracking-[0.14em] text-primary-600 dark:text-primary-300">
            最新 AI 对话
          </p>
          <p v-if="latestConversation" class="mt-1 max-w-[230px] truncate text-sm font-bold">
            {{ conversationTitle(latestConversation) }}
          </p>
        </div>
        <span
          v-if="latestConversation"
          class="shrink-0 rounded-full px-2 py-1 text-[10px] font-bold"
          :class="statusClass(displayStatus)"
        >
          {{ statusText(displayStatus) }}
        </span>
      </header>

      <div class="p-4">
        <p v-if="loading && !latestConversation" class="text-sm text-slate-500 dark:text-accent-300">
          正在读取最新对话……
        </p>
        <p v-else-if="error" class="text-sm text-rose-600 dark:text-rose-300">{{ error }}</p>
        <p v-else-if="!latestConversation" class="text-sm text-slate-500 dark:text-accent-300">
          暂无 AI 对话记录。
        </p>
        <template v-else>
          <div class="mb-3 flex items-center justify-between gap-3 text-[11px] text-slate-500 dark:text-accent-300">
            <span class="truncate">
              {{ latestConversation.provider_id }} · {{ latestConversation.model || latestConversation.model_id || '-' }}
            </span>
            <span class="shrink-0">{{ formatTime(latestConversation.updated_at) }}</span>
          </div>
          <div class="rounded-xl bg-slate-100 p-3 dark:bg-dark-950">
            <div class="mb-2 flex items-center gap-2 text-xs font-bold text-slate-600 dark:text-accent-200">
              <span
                class="size-2 rounded-full"
                :class="progressDotClass(displayStatus)"
              />
              <span>{{ liveStageText }}</span>
            </div>
            <pre
              ref="outputElement"
              data-testid="ai-work-output"
              class="max-h-56 overflow-auto whitespace-pre-wrap break-words font-sans text-xs leading-5"
            >{{ progressText }}</pre>
          </div>
        </template>
      </div>
    </section>

    <a
      href="/aiWork"
      target="_blank"
      rel="noopener noreferrer"
      aria-label="打开 AI 对话记录"
      aria-controls="ai-work-latest-panel"
      :aria-expanded="panelVisible"
      class="flex size-14 items-center justify-center rounded-full border border-white/60 bg-primary-600 text-white shadow-xl shadow-primary-950/25 transition duration-200 hover:-translate-y-1 hover:bg-primary-500 hover:shadow-2xl hover:shadow-primary-950/30 focus:outline-none focus-visible:ring-4 focus-visible:ring-primary-300 dark:border-primary-300/20 dark:bg-primary-500 dark:hover:bg-primary-400"
    >
      <svg
        aria-hidden="true"
        viewBox="0 0 24 24"
        fill="none"
        class="size-7"
        stroke="currentColor"
        stroke-width="1.8"
        stroke-linecap="round"
        stroke-linejoin="round"
      >
        <path d="M7.5 17.5 4 20v-4.3A7.5 7.5 0 0 1 2.5 11C2.5 6.9 6.5 3.5 11.5 3.5S20.5 6.9 20.5 11s-4 7.5-9 7.5c-1.45 0-2.8-.28-4-.78Z" />
        <path d="M8 10.75h7" />
        <path d="M8 13.75h4.5" />
        <path d="M18.5 17.5v3" />
        <path d="M17 19h3" />
      </svg>
    </a>
  </div>
</template>
