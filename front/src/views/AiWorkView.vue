<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import {
  aiWorkRawUrl,
  fetchAiWorkConversation,
  fetchAiWorkConversations,
  waitForAiWorkEvents,
} from '@/api/aiWork'
import type { AiWorkConversationSummary, AiWorkEvent } from '@/types/aiWork'
import { formatAiWorkError } from '@/utils/aiWorkError'

type ViewTab = 'conversation' | 'request' | 'result' | 'events'

const route = useRoute()
const conversations = ref<AiWorkConversationSummary[]>([])
const selectedId = ref('')
const selectedEvents = ref<AiWorkEvent[]>([])
const loadingList = ref(false)
const loadingConversation = ref(false)
const error = ref('')
const activeTab = ref<ViewTab>('conversation')
const outputElement = ref<HTMLElement | null>(null)
const reasoningElement = ref<HTMLElement | null>(null)
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

const providerRequestPayloads = computed(() =>
  customEvents.value
    .filter((event) => event.name === 'provider.request' || event.name === 'capability_probe.request')
    .map((event) => event.value)
    .filter((value): value is Record<string, unknown> => Boolean(value && typeof value === 'object')),
)

const agentRequestPayloads = computed(() =>
  customEvents.value
    .filter((event) => event.name === 'agent.request')
    .map((event) => event.value)
    .filter((value): value is Record<string, unknown> => Boolean(value && typeof value === 'object')),
)

const agentTranscriptMessages = computed(() => {
  const event = [...customEvents.value].reverse().find((item) => item.name === 'agent.transcript')
  const payload = event?.value
  if (!payload || typeof payload !== 'object') return []
  const rows = (payload as Record<string, unknown>).messages
  return Array.isArray(rows)
    ? rows.filter((item): item is Record<string, unknown> => Boolean(item && typeof item === 'object'))
    : []
})

const probeFlowEvents = computed(() =>
  customEvents.value.filter((event) => [
    'capability_probe.tool_call',
    'capability_probe.tool_result',
    'capability_probe.image_result',
    'capability_probe.browser_result',
  ].includes(String(event.name || ''))),
)

const toolFlowEvents = computed(() =>
  customEvents.value.filter((event) => [
    'TOOL_CALL_STARTED',
    'TOOL_CALL_FINISHED',
  ].includes(String(event.name || ''))),
)

const conversationFlowEvents = computed(() => [
  ...probeFlowEvents.value,
  ...(agentTranscriptMessages.value.length ? [] : toolFlowEvents.value),
])

const requestedConversationId = computed(() => {
  const value = route.query.conversation_id
  return String(Array.isArray(value) ? value[0] || '' : value || '').trim()
})

const messages = computed(() => {
  if (agentTranscriptMessages.value.length) return []
  const request = agentRequestPayloads.value[0] || providerRequestPayloads.value[0]
  const rows = Array.isArray(request?.messages) ? request.messages : []
  return rows
    .filter((item): item is Record<string, unknown> => Boolean(item && typeof item === 'object'))
    .map((item) => ({
      role: String(item.role || 'user'),
      content: String(item.content || ''),
    }))
})

const legacyAgentRequest = computed<Record<string, unknown> | null>(() => {
  const started = selectedEvents.value.find((event) => event.type === 'RUN_STARTED')
  if (!started || !started.rawEvent || typeof started.rawEvent !== 'object') return null
  const metadata = started.rawEvent as Record<string, unknown>
  if (metadata.capability !== 'agent') return null
  return {
    notice: '该记录创建时尚未保存 Pydantic Agent 的逐轮请求，以下为当时保留下来的运行输入摘要。',
    input: started.input,
    metadata,
  }
})

const requestPayloads = computed<Record<string, unknown>[]>(() => {
  const transcriptRequests = agentTranscriptMessages.value.filter((item) => item.kind === 'request')
  if (transcriptRequests.length) {
    return [
      ...providerRequestPayloads.value,
      ...agentRequestPayloads.value,
      ...transcriptRequests,
    ]
  }
  const current = [...providerRequestPayloads.value, ...agentRequestPayloads.value]
  if (current.length) return current
  return legacyAgentRequest.value ? [legacyAgentRequest.value] : []
})

const assistantOutput = computed(() =>
  selectedEvents.value
    .filter((event) => event.type === 'TEXT_MESSAGE_CONTENT')
    .map((event) => event.delta || '')
    .join(''),
)

const reasoningOutput = computed(() => {
  let output = ''
  for (const event of selectedEvents.value) {
    if (event.type === 'REASONING_MESSAGE_START' && output) output += '\n\n'
    if (event.type === 'REASONING_MESSAGE_CONTENT') output += event.delta || ''
  }
  return output
})

const reasoningCharacterCount = computed(() => selectedEvents.value
  .filter((event) => event.type === 'REASONING_MESSAGE_CONTENT')
  .reduce((total, event) => total + (event.delta || '').length, 0))

const reasoningStarted = computed(() => selectedEvents.value.some(
  (event) => event.type === 'REASONING_MESSAGE_START',
))

const reasoningEnded = computed(() => {
  const start = [...selectedEvents.value].reverse().find(
    (event) => event.type === 'REASONING_MESSAGE_START',
  )
  const end = [...selectedEvents.value].reverse().find(
    (event) => event.type === 'REASONING_MESSAGE_END',
  )
  return Boolean(start && end && end.seq > start.seq)
})

const providerRequestRecorded = computed(() => customEvents.value.some((event) => [
  'provider.request',
  'capability_probe.request',
  'agent.request',
].includes(String(event.name || ''))))

const runError = computed(() =>
  [...selectedEvents.value].reverse().find((event) => event.type === 'RUN_ERROR'),
)

const parsedResult = computed(() => {
  const event = [...customEvents.value].reverse().find((item) => item.name === 'business.result')
  if (event) return event.value
  const terminal = [...selectedEvents.value].reverse().find((item) => (
    item.type === 'RUN_FINISHED' || item.type === 'RUN_ERROR'
  ))
  if (terminal?.type === 'RUN_FINISHED') return terminal.result
  if (terminal?.type === 'RUN_ERROR') {
    return {
      status: 'failed',
      code: terminal.code || 'AI_RUN_FAILED',
      message: terminal.message || 'AI 执行失败',
      trace_id: terminal.trace_id || '',
      run_id: terminal.run_id || '',
    }
  }
  return undefined
})

const conversationOutput = computed(() => {
  if (runError.value) return formatAiWorkError(runError.value)
  if (assistantOutput.value) return assistantOutput.value
  const result = parsedResult.value
  if (result && typeof result === 'object') {
    const generatedCount = Number((result as Record<string, unknown>).generated_count)
    if (Number.isFinite(generatedCount)) {
      return `图片任务已完成，共生成 ${generatedCount} 张图片。`
    }
  }
  if (result !== undefined) return pretty(result)
  if (selectedConversation.value?.status === 'completed') return 'Provider 已返回，任务已完成。'
  if (reasoningStarted.value) return '模型正在推理，最终结果将在推理完成后显示。'
  if (providerRequestRecorded.value) return '请求已发送，正在等待 Provider 响应……'
  return '正在准备 Provider 请求……'
})

const lastSeq = computed(() =>
  selectedEvents.value.reduce((highest, event) => Math.max(highest, event.seq || 0), 0),
)

const conversationPhase = computed(() => {
  if (runError.value) return '执行失败'
  if (selectedConversation.value?.status === 'waiting_approval') return '等待人工审批'
  if (selectedEvents.value.some((event) => event.type === 'RUN_FINISHED')) return '任务完成'
  if (assistantOutput.value || selectedEvents.value.some((event) => event.type === 'TEXT_MESSAGE_START')) {
    return '正在生成结果'
  }
  if (reasoningStarted.value && !reasoningEnded.value) return '正在推理'
  if (reasoningStarted.value) return '正在整理推理结果'
  if (providerRequestRecorded.value) return '等待 Provider 响应'
  return '正在准备请求'
})

function pretty(value: unknown): string {
  if (value === undefined) return '暂无数据'
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

function transcriptLabel(value: Record<string, unknown>, index: number): string {
  const kind = String(value.kind || 'message')
  if (kind === 'request') return `Pydantic 模型请求 #${index + 1}`
  if (kind === 'response') return `模型响应 #${index + 1}`
  return `${kind} #${index + 1}`
}

function transcriptClass(value: Record<string, unknown>): string {
  return value.kind === 'response'
    ? 'border-primary-200 bg-primary-50 dark:border-primary-500/30 dark:bg-primary-500/10'
    : 'border-slate-200 bg-slate-50 dark:border-dark-700 dark:bg-dark-800'
}

function requestLabel(value: Record<string, unknown>, index: number): string {
  if (value.kind === 'request') {
    const requestIndex = requestPayloads.value
      .slice(0, index + 1)
      .filter((item) => item.kind === 'request')
      .length
    return `Pydantic 模型请求 #${requestIndex}`
  }
  if (value.mode) return 'Agent 初始输入与执行约束'
  if (value.notice) return '历史 Agent 输入摘要'
  return `Provider Request #${index + 1}`
}

function shortId(value: string): string {
  if (value.length <= 28) return value
  return `${value.slice(0, 17)}…${value.slice(-8)}`
}

function conversationTitle(conversation: AiWorkConversationSummary): string {
  if (conversation.use_case_id === 'config.ai_model_probe') {
    return `能力探测 · ${conversation.capability || '未知能力'}`
  }
  return conversation.use_case_id || conversation.capability
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
    waiting_approval: '等待审批',
    completed: '已完成',
    failed: '失败',
    interrupted: '已中断',
  }[status]
}

function statusClass(status: AiWorkConversationSummary['status']): string {
  if (status === 'running') return 'bg-sky-100 text-sky-700 dark:bg-sky-500/15 dark:text-sky-200'
  if (status === 'waiting_approval') return 'bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-200'
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
      const requestedId = requestedConversationId.value
      const target = conversations.value.find((item) => item.conversation_id === requestedId)
        || conversations.value[0]
      await selectConversation(target.conversation_id)
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
  while (
    generation === pollGeneration
    && selectedId.value === conversationId
    && selectedConversation.value
    && !['waiting_approval', 'completed', 'failed', 'interrupted'].includes(
      selectedConversation.value.status,
    )
  ) {
    try {
      const events = await waitForAiWorkEvents(conversationId, lastSeq.value)
      if (generation !== pollGeneration || selectedId.value !== conversationId) return
      if (events.length) {
        const known = new Set(selectedEvents.value.map((event) => event.seq))
        selectedEvents.value.push(...events.filter((event) => !known.has(event.seq)))
        void refreshConversations()
        if (events.some((event) => event.type === 'RUN_DEFERRED')) return
      }
    } catch (cause) {
      if (generation !== pollGeneration) return
      error.value = cause instanceof Error ? cause.message : String(cause)
      await new Promise((resolve) => window.setTimeout(resolve, 1_000))
    }
  }
}

watch([assistantOutput, reasoningOutput], async () => {
  await nextTick()
  if (outputElement.value) {
    outputElement.value.scrollTop = outputElement.value.scrollHeight
  }
  if (reasoningElement.value) {
    reasoningElement.value.scrollTop = reasoningElement.value.scrollHeight
  }
})

onMounted(() => {
  void refreshConversations(true)
  listTimer = window.setInterval(() => void refreshConversations(), 1_000)
})

watch(requestedConversationId, (conversationId) => {
  if (conversationId && conversationId !== selectedId.value) {
    void selectConversation(conversationId)
  }
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
              <p class="truncate text-sm font-bold">{{ conversationTitle(conversation) }}</p>
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
                <h2 class="truncate text-base font-black">{{ conversationTitle(selectedConversation) }}</h2>
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

            <article
              v-for="(step, index) in agentTranscriptMessages"
              :key="`agent-step-${index}`"
              class="rounded-2xl border p-4"
              :class="transcriptClass(step)"
            >
              <div class="mb-2 flex items-center justify-between gap-3">
                <p class="text-xs font-black uppercase tracking-wide text-slate-600 dark:text-accent-200">
                  {{ transcriptLabel(step, index) }}
                </p>
                <span class="text-[11px] text-slate-400">{{ step.state || 'complete' }}</span>
              </div>
              <pre class="overflow-auto whitespace-pre-wrap break-words text-xs leading-5">{{ pretty(step) }}</pre>
            </article>

            <article
              v-for="event in conversationFlowEvents"
              :key="event.seq"
              class="rounded-2xl border border-violet-200 bg-violet-50 p-4 dark:border-violet-500/30 dark:bg-violet-500/10"
            >
              <p class="mb-2 text-xs font-black uppercase tracking-wide text-violet-700 dark:text-violet-200">
                {{ String(event.name || '').replace('capability_probe.', '') }}
              </p>
              <pre class="overflow-auto whitespace-pre-wrap break-words text-xs leading-5">{{ pretty(event.value) }}</pre>
            </article>

            <details
              v-if="reasoningStarted || reasoningOutput"
              :open="!assistantOutput"
              class="rounded-2xl border border-violet-200 bg-violet-50 dark:border-violet-500/30 dark:bg-violet-500/10"
              data-testid="ai-work-reasoning"
            >
              <summary class="flex cursor-pointer items-center justify-between gap-3 px-4 py-3 text-xs font-black text-violet-700 dark:text-violet-200">
                <span>思考过程</span>
                <span class="font-bold">
                  {{ reasoningEnded ? '推理完成' : '正在推理' }} · {{ reasoningCharacterCount }} 字符
                </span>
              </summary>
              <pre
                ref="reasoningElement"
                class="max-h-[40vh] overflow-auto border-t border-violet-200 px-4 py-3 whitespace-pre-wrap break-words font-sans text-sm leading-6 dark:border-violet-500/30"
              >{{ reasoningOutput || 'Provider 已进入推理阶段，正在等待推理内容……' }}</pre>
            </details>

            <article class="rounded-2xl border border-primary-200 bg-primary-50 p-4 dark:border-primary-500/30 dark:bg-primary-500/10">
              <div class="mb-2 flex items-center justify-between gap-3">
                <p class="text-xs font-black uppercase tracking-wide text-primary-700 dark:text-primary-200">assistant</p>
                <span class="text-xs font-bold text-primary-600">{{ conversationPhase }}</span>
              </div>
              <pre ref="outputElement" class="max-h-[55vh] overflow-auto whitespace-pre-wrap break-words font-sans text-sm leading-6">{{ conversationOutput }}</pre>
            </article>

            <article v-if="runError" class="rounded-2xl border border-rose-200 bg-rose-50 p-4 text-rose-700">
              <p class="text-xs font-black uppercase tracking-wide">error · {{ runError.code }}</p>
              <pre class="mt-2 whitespace-pre-wrap font-sans text-sm">{{ runError.message }}</pre>
            </article>
          </div>

          <div v-else-if="activeTab === 'request'" class="space-y-4">
            <article v-for="(request, index) in requestPayloads" :key="index" class="rounded-xl bg-slate-950 p-4 text-slate-100">
              <p class="mb-3 text-xs font-bold text-slate-400">{{ requestLabel(request, index) }}</p>
              <pre class="overflow-auto whitespace-pre-wrap break-words text-xs leading-5">{{ pretty(request) }}</pre>
            </article>
            <p v-if="!requestPayloads.length" class="text-sm text-slate-500">当前记录没有可展示的请求数据。</p>
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
