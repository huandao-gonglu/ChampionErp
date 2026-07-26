<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  aiWorkRawUrl,
  fetchAiWorkConversation,
  fetchAiWorkConversations,
  waitForAiWorkEvents,
} from '@/api/aiWork'
import type { AiWorkConversationSummary, AiWorkEvent } from '@/types/aiWork'

type ViewTab = 'conversation' | 'request' | 'result' | 'events'

const conversations = ref<AiWorkConversationSummary[]>([])
const selectedId = ref('')
const selectedEvents = ref<AiWorkEvent[]>([])
const loadingList = ref(false)
const loadingConversation = ref(false)
const error = ref('')
const activeTab = ref<ViewTab>('conversation')
const outputElement = ref<HTMLElement | null>(null)
const tabs: Array<{ value: ViewTab; label: string }> = [
  { value: 'conversation', label: '对话' },
  { value: 'request', label: '原始请求' },
  { value: 'result', label: '处理结果' },
  { value: 'events', label: '事件' },
]

let listTimer: number | null = null
let pollGeneration = 0

const selectedConversation = computed(
  () => conversations.value.find((item) => item.conversation_id === selectedId.value) || null,
)

const customEvents = computed(() =>
  selectedEvents.value.filter((event) => event.type === 'CUSTOM'),
)

const providerRequests = computed(() =>
  customEvents.value
    .filter((event) => event.name === 'provider.request')
    .map((event) => event.value)
    .filter((value): value is Record<string, unknown> => Boolean(value && typeof value === 'object')),
)

const messages = computed(() => {
  const request = providerRequests.value[0]
  const rows = Array.isArray(request?.messages) ? request.messages : []
  return rows
    .filter((item): item is Record<string, unknown> => Boolean(item && typeof item === 'object'))
    .map((item) => ({
      role: String(item.role || 'user'),
      content: String(item.content || ''),
    }))
})

const assistantOutput = computed(() =>
  selectedEvents.value
    .filter((event) => event.type === 'TEXT_MESSAGE_CONTENT')
    .map((event) => event.delta || '')
    .join(''),
)

const parsedResult = computed(() => {
  const event = [...customEvents.value].reverse().find((item) => item.name === 'business.result')
  return event?.value
})

const runError = computed(() =>
  [...selectedEvents.value].reverse().find((event) => event.type === 'RUN_ERROR'),
)

const lastSeq = computed(() =>
  selectedEvents.value.reduce((highest, event) => Math.max(highest, event.seq || 0), 0),
)

function pretty(value: unknown): string {
  if (value === undefined) return '暂无数据'
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

function shortId(value: string): string {
  if (value.length <= 28) return value
  return `${value.slice(0, 17)}…${value.slice(-8)}`
}

function formatTime(value: string): string {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString()
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

async function refreshConversations(selectFirst = false) {
  if (loadingList.value) return
  loadingList.value = true
  try {
    const response = await fetchAiWorkConversations()
    conversations.value = response.conversations || []
    if ((selectFirst || !selectedId.value) && conversations.value.length) {
      await selectConversation(conversations.value[0].conversation_id)
    }
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause)
  } finally {
    loadingList.value = false
  }
}

async function selectConversation(conversationId: string) {
  if (!conversationId) return
  pollGeneration += 1
  const generation = pollGeneration
  selectedId.value = conversationId
  selectedEvents.value = []
  loadingConversation.value = true
  error.value = ''
  try {
    const response = await fetchAiWorkConversation(conversationId)
    if (generation !== pollGeneration) return
    selectedEvents.value = response.events || []
    void pollSelectedConversation(conversationId, generation)
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause)
  } finally {
    if (generation === pollGeneration) loadingConversation.value = false
  }
}

async function pollSelectedConversation(conversationId: string, generation: number) {
  while (generation === pollGeneration && selectedId.value === conversationId) {
    try {
      const events = await waitForAiWorkEvents(conversationId, lastSeq.value)
      if (generation !== pollGeneration || selectedId.value !== conversationId) return
      if (events.length) {
        const known = new Set(selectedEvents.value.map((event) => event.seq))
        selectedEvents.value.push(...events.filter((event) => !known.has(event.seq)))
        void refreshConversations()
      }
    } catch (cause) {
      if (generation !== pollGeneration) return
      error.value = cause instanceof Error ? cause.message : String(cause)
      await new Promise((resolve) => window.setTimeout(resolve, 1_000))
    }
  }
}

watch(assistantOutput, async () => {
  await nextTick()
  if (outputElement.value) {
    outputElement.value.scrollTop = outputElement.value.scrollHeight
  }
})

onMounted(() => {
  void refreshConversations(true)
  listTimer = window.setInterval(() => void refreshConversations(), 1_000)
})

onBeforeUnmount(() => {
  pollGeneration += 1
  if (listTimer !== null) window.clearInterval(listTimer)
})
</script>

<template>
  <main class="min-h-screen bg-slate-100 p-4 text-slate-900 dark:bg-dark-950 dark:text-white">
    <section class="mx-auto flex h-[calc(100vh-2rem)] max-w-[1800px] overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-xl dark:border-dark-700 dark:bg-dark-900">
      <aside class="flex w-[340px] shrink-0 flex-col border-r border-slate-200 dark:border-dark-700">
        <header class="border-b border-slate-200 p-4 dark:border-dark-700">
          <div class="flex items-center justify-between gap-3">
            <div>
              <h1 class="text-lg font-black">AI Work</h1>
              <p class="mt-1 text-xs text-slate-500 dark:text-accent-300">AIProvider 对话记录</p>
            </div>
            <button class="btn btn-outline px-3 py-1.5 text-xs" :disabled="loadingList" @click="refreshConversations()">
              刷新
            </button>
          </div>
        </header>

        <div class="min-h-0 flex-1 overflow-y-auto">
          <button
            v-for="conversation in conversations"
            :key="conversation.conversation_id"
            class="w-full border-b border-slate-100 p-4 text-left transition hover:bg-slate-50 dark:border-dark-800 dark:hover:bg-dark-800"
            :class="{ 'bg-primary-50 dark:bg-primary-500/10': selectedId === conversation.conversation_id }"
            @click="selectConversation(conversation.conversation_id)"
          >
            <div class="flex items-start justify-between gap-2">
              <p class="truncate text-sm font-bold">{{ conversation.use_case_id || conversation.capability }}</p>
              <span class="rounded-full px-2 py-1 text-[10px] font-bold" :class="statusClass(conversation.status)">
                {{ statusText(conversation.status) }}
              </span>
            </div>
            <p class="mt-2 truncate text-xs text-slate-600 dark:text-accent-200">
              {{ conversation.provider_id }} · {{ conversation.model || conversation.model_id || '-' }}
            </p>
            <div class="mt-2 flex items-center justify-between text-[11px] text-slate-400">
              <span :title="conversation.conversation_id">{{ shortId(conversation.conversation_id) }}</span>
              <span>{{ formatTime(conversation.updated_at) }}</span>
            </div>
          </button>
          <p v-if="!conversations.length && !loadingList" class="p-8 text-center text-sm text-slate-500">
            暂无 AI 对话。执行任意 AI 功能后会自动出现。
          </p>
        </div>
      </aside>

      <section class="flex min-w-0 flex-1 flex-col">
        <header class="border-b border-slate-200 px-5 py-4 dark:border-dark-700">
          <div v-if="selectedConversation" class="flex flex-wrap items-center justify-between gap-3">
            <div class="min-w-0">
              <div class="flex flex-wrap items-center gap-2">
                <h2 class="truncate text-base font-black">{{ selectedConversation.use_case_id }}</h2>
                <span class="rounded-full px-2 py-1 text-[10px] font-bold" :class="statusClass(selectedConversation.status)">
                  {{ statusText(selectedConversation.status) }}
                </span>
                <span v-if="selectedConversation.stream" class="badge-info">流式</span>
              </div>
              <p class="mt-1 text-xs text-slate-500 dark:text-accent-300">
                {{ selectedConversation.provider_id }} · {{ selectedConversation.model || '-' }} ·
                {{ selectedConversation.conversation_id }}
              </p>
            </div>
            <a class="btn btn-outline px-3 py-1.5 text-xs" :href="aiWorkRawUrl(selectedConversation.conversation_id)" target="_blank">
              查看原始 JSONL
            </a>
          </div>
          <p v-else class="text-sm text-slate-500">请选择一个对话。</p>
        </header>

        <nav v-if="selectedConversation" class="flex gap-1 border-b border-slate-200 px-5 pt-3 dark:border-dark-700">
          <button
            v-for="tab in tabs"
            :key="tab.value"
            class="rounded-t-lg px-4 py-2 text-sm font-bold"
            :class="activeTab === tab.value ? 'bg-slate-100 text-primary-700 dark:bg-dark-800 dark:text-primary-200' : 'text-slate-500'"
            @click="activeTab = tab.value"
          >
            {{ tab.label }}
          </button>
        </nav>

        <div v-if="error" class="m-5 rounded-lg bg-rose-50 p-4 text-sm text-rose-700 ring-1 ring-rose-200">
          {{ error }}
        </div>

        <div v-if="selectedConversation" class="min-h-0 flex-1 overflow-y-auto p-5">
          <div v-if="loadingConversation" class="py-12 text-center text-sm text-slate-500">正在读取对话……</div>

          <div v-else-if="activeTab === 'conversation'" class="mx-auto max-w-5xl space-y-4">
            <article
              v-for="(message, index) in messages"
              :key="`${message.role}-${index}`"
              class="rounded-2xl border p-4"
              :class="message.role === 'system'
                ? 'border-amber-200 bg-amber-50 dark:border-amber-500/30 dark:bg-amber-500/10'
                : 'border-slate-200 bg-slate-50 dark:border-dark-700 dark:bg-dark-800'"
            >
              <p class="mb-2 text-xs font-black uppercase tracking-wide text-slate-500">{{ message.role }}</p>
              <pre class="whitespace-pre-wrap break-words font-sans text-sm leading-6">{{ message.content }}</pre>
            </article>

            <article class="rounded-2xl border border-primary-200 bg-primary-50 p-4 dark:border-primary-500/30 dark:bg-primary-500/10">
              <div class="mb-2 flex items-center justify-between gap-3">
                <p class="text-xs font-black uppercase tracking-wide text-primary-700 dark:text-primary-200">assistant</p>
                <span v-if="selectedConversation.status === 'running'" class="text-xs font-bold text-primary-600">正在输出</span>
              </div>
              <pre ref="outputElement" class="max-h-[55vh] overflow-auto whitespace-pre-wrap break-words font-sans text-sm leading-6">{{ assistantOutput || '等待 Provider 返回……' }}</pre>
            </article>

            <article v-if="runError" class="rounded-2xl border border-rose-200 bg-rose-50 p-4 text-rose-700">
              <p class="text-xs font-black uppercase tracking-wide">error · {{ runError.code }}</p>
              <pre class="mt-2 whitespace-pre-wrap font-sans text-sm">{{ runError.message }}</pre>
            </article>
          </div>

          <div v-else-if="activeTab === 'request'" class="space-y-4">
            <article v-for="(request, index) in providerRequests" :key="index" class="rounded-xl bg-slate-950 p-4 text-slate-100">
              <p class="mb-3 text-xs font-bold text-slate-400">Provider Request #{{ index + 1 }}</p>
              <pre class="overflow-auto whitespace-pre-wrap break-words text-xs leading-5">{{ pretty(request) }}</pre>
            </article>
            <p v-if="!providerRequests.length" class="text-sm text-slate-500">Provider 尚未生成最终请求。</p>
          </div>

          <div v-else-if="activeTab === 'result'">
            <pre class="overflow-auto whitespace-pre-wrap break-words rounded-xl bg-slate-950 p-4 text-xs leading-5 text-slate-100">{{ pretty(parsedResult) }}</pre>
          </div>

          <div v-else class="space-y-3">
            <article v-for="event in selectedEvents" :key="event.seq" class="rounded-xl border border-slate-200 p-3 dark:border-dark-700">
              <div class="flex items-center justify-between gap-3 text-xs">
                <strong>#{{ event.seq }} · {{ event.type }}<template v-if="event.name"> · {{ event.name }}</template></strong>
                <span class="text-slate-400">{{ formatTime(event.occurred_at) }}</span>
              </div>
              <pre class="mt-2 overflow-auto whitespace-pre-wrap break-words text-xs text-slate-600 dark:text-accent-200">{{ pretty(event) }}</pre>
            </article>
          </div>
        </div>
      </section>
    </section>
  </main>
</template>
