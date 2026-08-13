<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import {
  aiWorkRawUrl,
  fetchAiWorkConversation,
  fetchAiWorkConversationChildren,
  fetchAiWorkConversations,
  waitForAiWorkEvents,
} from '@/api/aiWork'
import GlobalAgentChatPanel from '@/components/ai-work/GlobalAgentChatPanel.vue'
import type { AiWorkConversationSummary, AiWorkEvent } from '@/types/aiWork'
import { formatAiWorkError } from '@/utils/aiWorkError'

type ViewTab = 'conversation' | 'request' | 'result' | 'events'

const route = useRoute()
const conversations = ref<AiWorkConversationSummary[]>([])
const selectedId = ref('')
const selectedSummary = ref<AiWorkConversationSummary | null>(null)
const selectedEvents = ref<AiWorkEvent[]>([])
const executionConversations = ref<AiWorkConversationSummary[]>([])
const executionConversationsState = ref<'loading' | 'loaded' | 'failed'>('loaded')
const loadingList = ref(false)
const loadingConversation = ref(false)
const error = ref('')
const activeTab = ref<ViewTab>('conversation')
const isNewConversation = ref(false)
const newConversationId = ref('')
const SHOW_INTERNAL_STORAGE_KEY = 'ai-work.show-internal-conversations'
const showInternalConversations = ref(readShowInternalConversations())
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
let pollAbortController: AbortController | null = null

interface SidebarConversation {
  conversation: AiWorkConversationSummary
  depth: number
  internal: boolean
}

const selectedConversation = computed(() => selectedSummary.value)

const rootConversations = computed(() => conversations.value.filter((item) => (
  !item.parent_conversation_id
)))

const sidebarConversations = computed<SidebarConversation[]>(() => {
  if (!showInternalConversations.value) {
    return rootConversations.value.map((conversation) => ({
      conversation,
      depth: 0,
      internal: false,
    }))
  }
  const children = new Map<string, AiWorkConversationSummary[]>()
  for (const conversation of conversations.value) {
    if (!conversation.parent_conversation_id) continue
    const rows = children.get(conversation.parent_conversation_id) || []
    rows.push(conversation)
    children.set(conversation.parent_conversation_id, rows)
  }
  const rows: SidebarConversation[] = []
  const appended = new Set<string>()
  const append = (conversation: AiWorkConversationSummary, depth: number) => {
    if (appended.has(conversation.conversation_id)) return
    appended.add(conversation.conversation_id)
    rows.push({ conversation, depth, internal: depth > 0 })
    for (const child of children.get(conversation.conversation_id) || []) {
      append(child, depth + 1)
    }
  }
  // 根会话始终保持 API 返回的最近排序；子会话只收纳在所属根会话下。
  for (const conversation of rootConversations.value) append(conversation, 0)
  for (const conversation of conversations.value) {
    if (!appended.has(conversation.conversation_id)) append(conversation, 1)
  }
  return rows
})

const selectedSidebarId = computed(() => (
  showInternalConversations.value
    ? selectedId.value
    : selectedConversation.value?.parent_conversation_id || selectedId.value
))

const parentConversation = computed(() => {
  const parentId = selectedConversation.value?.parent_conversation_id
  if (!parentId) return null
  return conversations.value.find((item) => item.conversation_id === parentId) || null
})

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
  if (conversation.use_case_id === 'global.agent.chat') {
    return '全局 Agent 对话'
  }
  if (conversation.use_case_id === 'global.task.plan') return '任务规划'
  if (conversation.use_case_id === 'category.product_match') return '类目匹配'
  if (conversation.use_case_id === 'category.attribute_fill') return '属性补全'
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

function conversationStatusText(conversation: AiWorkConversationSummary): string {
  if (conversation.use_case_id !== 'global.agent.chat' || !conversation.latest_task_status) {
    return statusText(conversation.status)
  }
  return {
    planning: '正在规划',
    running: '正在执行',
    needs_input: '等待补充资料',
    waiting_publish_confirmation: '等待发布确认',
    waiting_publish_result: '等待平台结果',
    completed: '任务已完成',
    failed: '任务失败',
    cancelled: '任务已取消',
  }[conversation.latest_task_status]
}

function statusClass(status: AiWorkConversationSummary['status']): string {
  if (status === 'running') return 'bg-sky-100 text-sky-700 dark:bg-sky-500/15 dark:text-sky-200'
  if (status === 'waiting_approval') return 'bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-200'
  if (status === 'completed') return 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-200'
  return 'bg-rose-100 text-rose-700 dark:bg-rose-500/15 dark:text-rose-200'
}

function conversationStatusClass(conversation: AiWorkConversationSummary): string {
  const taskStatus = conversation.use_case_id === 'global.agent.chat'
    ? conversation.latest_task_status
    : null
  if (!taskStatus) return statusClass(conversation.status)
  if (taskStatus === 'completed') {
    return 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-200'
  }
  if (taskStatus === 'failed' || taskStatus === 'cancelled') {
    return 'bg-rose-100 text-rose-700 dark:bg-rose-500/15 dark:text-rose-200'
  }
  if (taskStatus === 'needs_input' || taskStatus === 'waiting_publish_confirmation') {
    return 'bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-200'
  }
  return 'bg-sky-100 text-sky-700 dark:bg-sky-500/15 dark:text-sky-200'
}

async function refreshConversations(selectFirst = false) {
  if (loadingList.value) return
  loadingList.value = true
  try {
    const response = await fetchAiWorkConversations(50, showInternalConversations.value)
    conversations.value = (response.conversations || []).filter((item) => (
      showInternalConversations.value || !item.parent_conversation_id
    ))
    if (selectedId.value) {
      const refreshed = conversations.value.find((item) => (
        item.conversation_id === selectedId.value
      ))
      if (refreshed) selectedSummary.value = refreshed
    }
    if (isNewConversation.value && newConversationId.value) {
      const created = conversations.value.find(
        (item) => item.conversation_id === newConversationId.value,
      )
      if (created) {
        isNewConversation.value = false
        newConversationId.value = ''
        await selectConversation(created.conversation_id)
        return
      }
    }
    if (!isNewConversation.value && (selectFirst || !selectedId.value)) {
      const requestedId = requestedConversationId.value
      const target = conversations.value.find((item) => item.conversation_id === requestedId)
      if (requestedId && !target) {
        await selectConversation(requestedId)
      } else if (target || rootConversations.value.length) {
        await selectConversation((target || rootConversations.value[0]).conversation_id)
      }
    }
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause)
  } finally {
    loadingList.value = false
  }
}

async function selectConversation(
  conversationId: string,
  knownSummary: AiWorkConversationSummary | null = null,
) {
  if (!conversationId) return
  isNewConversation.value = false
  newConversationId.value = ''
  cancelActivePoll()
  const generation = pollGeneration
  selectedId.value = conversationId
  selectedSummary.value = knownSummary || conversations.value.find((item) => (
    item.conversation_id === conversationId
  )) || null
  selectedEvents.value = []
  executionConversations.value = []
  executionConversationsState.value = 'loading'
  loadingConversation.value = true
  error.value = ''
  try {
    const response = await fetchAiWorkConversation(conversationId)
    if (generation !== pollGeneration) return
    selectedSummary.value = response.conversation || selectedSummary.value
    selectedEvents.value = response.events || []
    if (selectedConversation.value?.use_case_id === 'global.agent.chat') {
      void refreshExecutionConversations(conversationId, generation)
    } else {
      executionConversationsState.value = 'loaded'
    }
    void pollSelectedConversation(conversationId, generation)
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause)
  } finally {
    if (generation === pollGeneration) loadingConversation.value = false
  }
}

function openNewConversation() {
  cancelActivePoll()
  selectedId.value = ''
  selectedSummary.value = null
  selectedEvents.value = []
  executionConversations.value = []
  executionConversationsState.value = 'loaded'
  loadingConversation.value = false
  error.value = ''
  activeTab.value = 'conversation'
  isNewConversation.value = true
  newConversationId.value = ''
}

async function handleGlobalConversationCreated(payload: { conversationId: string; taskId: string }) {
  newConversationId.value = payload.conversationId
  selectedId.value = payload.conversationId
  await Promise.all([
    refreshGlobalProjection(payload.conversationId),
    refreshConversations(),
  ])
}

async function refreshGlobalProjection(conversationId: string) {
  if (!conversationId) return
  try {
    const response = await fetchAiWorkConversation(conversationId)
    const currentId = isNewConversation.value ? newConversationId.value : selectedId.value
    if (currentId !== conversationId) return
    selectedEvents.value = response.events || []
    if (selectedConversation.value?.use_case_id === 'global.agent.chat') {
      void refreshExecutionConversations(conversationId, pollGeneration)
    }
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause)
  }
}

async function refreshExecutionConversations(conversationId: string, generation: number) {
  if (!conversationId) return
  executionConversationsState.value = 'loading'
  try {
    const response = await fetchAiWorkConversationChildren(conversationId, 200)
    if (generation !== pollGeneration || selectedId.value !== conversationId) return
    executionConversations.value = response.conversations || []
    executionConversationsState.value = 'loaded'
  } catch (cause) {
    if (generation !== pollGeneration || selectedId.value !== conversationId) return
    executionConversationsState.value = 'failed'
    error.value = cause instanceof Error ? cause.message : String(cause)
  }
}

async function openExecutionConversation(conversationId: string) {
  const summary = executionConversations.value.find((item) => (
    item.conversation_id === conversationId
  )) || conversations.value.find((item) => item.conversation_id === conversationId) || null
  activeTab.value = 'conversation'
  await selectConversation(conversationId, summary)
}

async function returnToParentConversation() {
  const parent = parentConversation.value
  const parentId = selectedConversation.value?.parent_conversation_id
  if (!parentId) return
  activeTab.value = 'conversation'
  await selectConversation(parentId, parent)
}

async function toggleInternalConversations() {
  writeShowInternalConversations(showInternalConversations.value)
  await refreshConversations()
}

function readShowInternalConversations(): boolean {
  if (typeof window === 'undefined') return false
  try {
    return window.localStorage.getItem(SHOW_INTERNAL_STORAGE_KEY) === 'true'
  } catch {
    return false
  }
}

function writeShowInternalConversations(value: boolean) {
  if (typeof window === 'undefined') return
  try {
    window.localStorage.setItem(SHOW_INTERNAL_STORAGE_KEY, String(value))
  } catch {
    // 隐私模式或受限 WebView 可能禁用 localStorage；开关在当前页面仍然有效。
  }
}

async function pollSelectedConversation(conversationId: string, generation: number) {
  const abortController = new AbortController()
  pollAbortController?.abort()
  pollAbortController = abortController
  try {
    while (
      generation === pollGeneration
      && selectedId.value === conversationId
      && selectedConversation.value
      && !['waiting_approval', 'completed', 'failed', 'interrupted'].includes(
        selectedConversation.value.status,
      )
    ) {
      try {
        const events = await waitForAiWorkEvents(
          conversationId,
          lastSeq.value,
          20_000,
          abortController.signal,
        )
        if (generation !== pollGeneration || selectedId.value !== conversationId) return
        if (events.length) {
          const known = new Set(selectedEvents.value.map((event) => event.seq))
          selectedEvents.value.push(...events.filter((event) => !known.has(event.seq)))
          if (selectedConversation.value?.use_case_id === 'global.agent.chat') {
            void refreshExecutionConversations(conversationId, generation)
          }
          void refreshConversations()
          if (events.some((event) => event.type === 'RUN_DEFERRED')) return
        }
      } catch (cause) {
        if (abortController.signal.aborted || generation !== pollGeneration) return
        error.value = cause instanceof Error ? cause.message : String(cause)
        await new Promise((resolve) => window.setTimeout(resolve, 1_000))
      }
    }
  } finally {
    if (pollAbortController === abortController) pollAbortController = null
  }
}

function cancelActivePoll() {
  pollGeneration += 1
  pollAbortController?.abort()
  pollAbortController = null
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
  cancelActivePoll()
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
          <button
            type="button"
            class="btn btn-primary mt-4 w-full"
            data-testid="new-global-conversation"
            @click="openNewConversation"
          >
            <span aria-hidden="true">＋</span>
            新建对话
          </button>
          <label class="mt-3 flex cursor-pointer items-center justify-between gap-3 text-xs text-slate-600 dark:text-accent-200">
            <span>显示内部执行会话</span>
            <input
              v-model="showInternalConversations"
              type="checkbox"
              class="h-4 w-4 rounded border-slate-300 text-primary-600 focus:ring-primary-500"
              data-testid="show-internal-conversations"
              @change="toggleInternalConversations"
            />
          </label>
        </header>

        <div class="min-h-0 flex-1 overflow-y-auto">
          <button
            v-for="row in sidebarConversations"
            :key="row.conversation.conversation_id"
            type="button"
            class="w-full border-b border-slate-100 p-4 text-left transition hover:bg-slate-50 dark:border-dark-800 dark:hover:bg-dark-800"
            :class="{
              'bg-primary-50 dark:bg-primary-500/10': selectedSidebarId === row.conversation.conversation_id,
              'border-l-4 border-l-slate-300 bg-slate-50/70 pl-7 dark:border-l-dark-600 dark:bg-dark-800/40': row.internal,
            }"
            :data-testid="row.internal ? `internal-conversation-${row.conversation.conversation_id}` : `root-conversation-${row.conversation.conversation_id}`"
            @click="selectConversation(row.conversation.conversation_id, row.conversation)"
          >
            <div class="flex items-start justify-between gap-2">
              <div class="flex min-w-0 items-center gap-2">
                <span v-if="row.internal" aria-hidden="true" class="text-slate-400">↳</span>
                <p class="truncate text-sm font-bold">{{ conversationTitle(row.conversation) }}</p>
              </div>
              <span class="rounded-full px-2 py-1 text-[10px] font-bold" :class="conversationStatusClass(row.conversation)">
                {{ conversationStatusText(row.conversation) }}
              </span>
            </div>
            <p v-if="row.internal" class="mt-1 text-[10px] font-black uppercase tracking-wide text-slate-400">
              内部执行会话
            </p>
            <p class="mt-2 truncate text-xs text-slate-600 dark:text-accent-200">
              {{ row.conversation.provider_id }} · {{ row.conversation.model || row.conversation.model_id || '-' }}
            </p>
            <div class="mt-2 flex items-center justify-between text-[11px] text-slate-400">
              <span :title="row.conversation.conversation_id">{{ shortId(row.conversation.conversation_id) }}</span>
              <span>{{ formatTime(row.conversation.updated_at) }}</span>
            </div>
          </button>
          <!--
            子 Agent 只在显式开关开启后参与这一渲染列表；默认根会话顺序来自
            后端 root-only API，内部执行不会改变用户最近对话的排序。
          -->
          <p v-if="!sidebarConversations.length && !loadingList" class="p-8 text-center text-sm text-slate-500">
            暂无 AI 对话。发送目标或执行任意 AI 功能后会自动出现。
          </p>
        </div>
      </aside>

      <section class="flex min-w-0 flex-1 flex-col">
        <header class="border-b border-slate-200 px-5 py-4 dark:border-dark-700">
          <div v-if="isNewConversation" class="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 class="text-base font-black">全局 Agent 新对话</h2>
              <p class="mt-1 text-xs text-slate-500 dark:text-accent-300">
                第一条消息发送后才会创建对话和任务记录。
              </p>
            </div>
            <span class="badge-muted">尚未创建</span>
          </div>
          <div v-else-if="selectedConversation" class="flex flex-wrap items-center justify-between gap-3">
            <div class="min-w-0">
              <div class="flex flex-wrap items-center gap-2">
                <h2 class="truncate text-base font-black">{{ conversationTitle(selectedConversation) }}</h2>
                <span class="rounded-full px-2 py-1 text-[10px] font-bold" :class="conversationStatusClass(selectedConversation)">
                  {{ conversationStatusText(selectedConversation) }}
                </span>
                <span v-if="selectedConversation.stream" class="badge-info">流式</span>
                <span v-if="selectedConversation.parent_conversation_id" class="badge-muted">内部执行 · 只读</span>
              </div>
              <p class="mt-1 text-xs text-slate-500 dark:text-accent-300">
                {{ selectedConversation.provider_id }} · {{ selectedConversation.model || '-' }} ·
                {{ selectedConversation.conversation_id }}
              </p>
            </div>
            <div class="flex flex-wrap gap-2">
              <button
                v-if="selectedConversation.parent_conversation_id"
                type="button"
                class="btn btn-outline px-3 py-1.5 text-xs"
                data-testid="return-to-parent-conversation"
                @click="returnToParentConversation"
              >
                返回主对话
              </button>
              <a class="btn btn-outline px-3 py-1.5 text-xs" :href="aiWorkRawUrl(selectedConversation.conversation_id)" target="_blank">
                查看原始 JSONL
              </a>
            </div>
          </div>
          <p v-else class="text-sm text-slate-500">请选择一个对话。</p>
        </header>

        <nav v-if="selectedConversation && !isNewConversation" class="flex gap-1 border-b border-slate-200 px-5 pt-3 dark:border-dark-700">
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

        <div v-if="isNewConversation" class="min-h-0 flex-1 overflow-hidden p-5">
          <GlobalAgentChatPanel
            :conversation-id="newConversationId"
            :events="selectedEvents"
            :execution-conversations="executionConversations"
            :execution-conversations-state="executionConversationsState"
            @conversation-created="handleGlobalConversationCreated"
            @open-execution="openExecutionConversation"
            @refresh-events="refreshGlobalProjection"
          />
        </div>

        <div v-else-if="selectedConversation" class="min-h-0 flex-1 overflow-y-auto p-5">
          <div v-if="loadingConversation" class="py-12 text-center text-sm text-slate-500">正在读取对话……</div>

          <GlobalAgentChatPanel
            v-else-if="activeTab === 'conversation' && selectedConversation.use_case_id === 'global.agent.chat'"
            :conversation-id="selectedConversation.conversation_id"
            :events="selectedEvents"
            :execution-conversations="executionConversations"
            :execution-conversations-state="executionConversationsState"
            @conversation-created="handleGlobalConversationCreated"
            @open-execution="openExecutionConversation"
            @refresh-events="refreshGlobalProjection"
          />

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
