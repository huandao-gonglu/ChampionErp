<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import {
  fetchAiWorkConversation,
  fetchAiWorkConversations,
  waitForAiWorkEvents,
} from '@/api/aiWork'
import type { AiWorkConversationSummary, AiWorkEvent } from '@/types/aiWork'

const route = useRoute()
const panelVisible = ref(false)
const loading = ref(false)
const error = ref('')
const latestConversation = ref<AiWorkConversationSummary | null>(null)
const latestEvents = ref<AiWorkEvent[]>([])
const outputElement = ref<HTMLElement | null>(null)
let pollGeneration = 0

const shouldRender = computed(() => route.name !== 'AiWork')
const lastSeq = computed(() =>
  latestEvents.value.reduce((highest, event) => Math.max(highest, event.seq || 0), 0),
)
const assistantOutput = computed(() =>
  latestEvents.value
    .filter((event) => event.type === 'TEXT_MESSAGE_CONTENT')
    .map((event) => event.delta || '')
    .join(''),
)
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
const liveStatus = computed<AiWorkConversationSummary['status']>(() => {
  if (latestEvents.value.some((event) => event.type === 'RUN_ERROR')) return 'failed'
  if (latestEvents.value.some((event) => event.type === 'RUN_FINISHED')) return 'completed'
  return latestConversation.value?.status || 'running'
})
const isTerminal = computed(() => ['completed', 'failed', 'interrupted'].includes(liveStatus.value))
const progressText = computed(() => {
  if (runError.value) return runError.value.message || 'AI 执行失败'
  if (assistantOutput.value) return assistantOutput.value
  if (businessResult.value !== undefined) return pretty(businessResult.value)
  if (latestEvents.value.some((event) => event.type === 'CUSTOM' && event.name === 'provider.request')) {
    return '请求已发送，正在等待 Provider 返回……'
  }
  return loading.value ? '正在读取最新对话……' : '正在准备 Provider 请求……'
})

function pretty(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

function statusText(status: AiWorkConversationSummary['status']): string {
  return {
    running: '进行中',
    completed: '已完成',
    failed: '失败',
    interrupted: '已中断',
  }[status]
}

function statusClass(status: AiWorkConversationSummary['status']): string {
  if (status === 'running') return 'bg-sky-100 text-sky-700 dark:bg-sky-500/15 dark:text-sky-200'
  if (status === 'completed') return 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-200'
  return 'bg-rose-100 text-rose-700 dark:bg-rose-500/15 dark:text-rose-200'
}

function formatTime(value: string): string {
  if (!value) return ''
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

async function pollConversation(conversationId: string, generation: number) {
  while (
    generation === pollGeneration
    && panelVisible.value
    && latestConversation.value?.conversation_id === conversationId
    && !isTerminal.value
  ) {
    try {
      const events = await waitForAiWorkEvents(conversationId, lastSeq.value, 5_000)
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
      if (generation !== pollGeneration || !panelVisible.value) return
      error.value = cause instanceof Error ? cause.message : String(cause)
      return
    }
  }
}

async function openPanel() {
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
    data-testid="ai-work-floating"
    class="fixed bottom-5 right-5 z-[70] flex flex-col items-end gap-3"
    @mouseenter="openPanel"
    @mouseleave="closePanel"
  >
    <section
      v-if="panelVisible"
      data-testid="ai-work-latest"
      class="w-[360px] max-w-[calc(100vw-2.5rem)] overflow-hidden rounded-2xl border border-slate-200 bg-white text-slate-900 shadow-2xl shadow-slate-950/20 dark:border-dark-700 dark:bg-dark-900 dark:text-white"
    >
      <header class="flex items-center justify-between gap-3 border-b border-slate-200 px-4 py-3 dark:border-dark-700">
        <div>
          <p class="text-xs font-black uppercase tracking-[0.14em] text-primary-600 dark:text-primary-300">
            最新 AI 对话
          </p>
          <p v-if="latestConversation" class="mt-1 max-w-[230px] truncate text-sm font-bold">
            {{ latestConversation.use_case_id || latestConversation.capability }}
          </p>
        </div>
        <span
          v-if="latestConversation"
          class="shrink-0 rounded-full px-2 py-1 text-[10px] font-bold"
          :class="statusClass(liveStatus)"
        >
          {{ statusText(liveStatus) }}
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
                :class="liveStatus === 'running' ? 'animate-pulse bg-sky-500' : liveStatus === 'completed' ? 'bg-emerald-500' : 'bg-rose-500'"
              />
              <span>{{ liveStatus === 'running' ? '实时输出' : '最终结果' }}</span>
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
