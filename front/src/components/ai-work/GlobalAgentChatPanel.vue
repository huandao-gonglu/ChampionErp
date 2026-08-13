<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import {
  cancelGlobalTask,
  confirmGlobalTaskPublish,
  fetchGlobalTaskState,
  startGlobalTask,
  submitGlobalTaskInput,
} from '@/api/globalTasks'
import type { AiWorkConversationSummary, AiWorkEvent } from '@/types/aiWork'
import type {
  GlobalTaskRequiredInput,
  GlobalTaskResponse,
  GlobalTaskState,
  GlobalTaskStatus,
  GlobalTaskStepStatus,
} from '@/types/globalTasks'

const props = withDefaults(defineProps<{
  conversationId?: string
  events?: AiWorkEvent[]
  executionConversations?: AiWorkConversationSummary[]
  executionConversationsState?: 'loading' | 'loaded' | 'failed'
}>(), {
  conversationId: '',
  events: () => [],
  executionConversations: () => [],
  executionConversationsState: 'loading',
})

const emit = defineEmits<{
  (event: 'conversation-created', payload: {
    conversationId: string
    taskId: string
  }): void
  (event: 'refresh-events', conversationId: string): void
  (event: 'open-execution', conversationId: string): void
}>()

interface ChatMessage {
  key: string
  taskId: string
  role: 'user' | 'assistant'
  message: string
}

interface AgentExecutionLink {
  key: string
  taskId: string
  conversationId: string
  summary: AiWorkConversationSummary | null
}

const POLL_INTERVAL_MS = 2_500
const POLLABLE_STATUSES = new Set<GlobalTaskStatus>([
  'planning',
  'running',
  'waiting_publish_result',
])
const TERMINAL_STATUSES = new Set<GlobalTaskStatus>([
  'completed',
  'failed',
  'cancelled',
])

const task = ref<GlobalTaskState | null>(null)
const activeTaskId = ref('')
const responseConversationId = ref('')
const recentSnapshotId = ref('')
const goal = ref('')
const inputMessage = ref('')
const inputValues = ref<Record<string, string | string[]>>({})
const optimisticMessages = ref<ChatMessage[]>([])
const busyAction = ref('')
const loadingTask = ref(false)
const error = ref('')

let pollTimer: number | null = null
let requestGeneration = 0
let optimisticSequence = 0

const currentConversationId = computed(() => (
  responseConversationId.value || props.conversationId
))

const projectedMessages = computed<ChatMessage[]>(() => {
  const messages: ChatMessage[] = []
  for (const event of props.events) {
    if (event.type !== 'CUSTOM') continue
    if (event.name !== 'global.user_message' && event.name !== 'global.assistant_message') continue
    const value = asRecord(event.value)
    const message = String(value?.message || '').trim()
    if (!message) continue
    messages.push({
      key: `event-${event.seq}`,
      taskId: String(value?.task_id || ''),
      role: event.name === 'global.user_message' ? 'user' : 'assistant',
      message,
    })
  }
  return messages
})

const messages = computed(() => {
  const values = [
    ...projectedMessages.value,
    ...optimisticMessages.value.filter((local) => !projectedMessages.value.some((projected) => (
      projected.role === local.role
      && projected.message === local.message
      && (!local.taskId || !projected.taskId || projected.taskId === local.taskId)
    ))),
  ]
  const assistantMessage = String(task.value?.assistant_message || '').trim()
  if (assistantMessage && !values.some((message) => (
    message.role === 'assistant'
    && message.taskId === task.value?.task_id
    && message.message === assistantMessage
  ))) {
    values.push({
      key: `task-assistant-${task.value?.task_id}`,
      taskId: task.value?.task_id || '',
      role: 'assistant',
      message: assistantMessage,
    })
  }
  return values
})

const latestProjectedTaskId = computed(() => {
  for (const event of [...props.events].reverse()) {
    if (event.type !== 'CUSTOM') continue
    if (![
      'global.user_message',
      'global.assistant_message',
      'global.task_state',
    ].includes(String(event.name || ''))) continue
    const taskId = String(asRecord(event.value)?.task_id || '').trim()
    if (taskId) return taskId
  }
  return ''
})

const agentExecutionLinks = computed<AgentExecutionLink[]>(() => {
  const seen = new Set<string>()
  const links: AgentExecutionLink[] = []
  for (const event of props.events) {
    if (event.type !== 'CUSTOM' || event.name !== 'global.agent_execution_link') continue
    const value = asRecord(event.value)
    const conversationId = String(value?.conversation_id || '').trim()
    if (!conversationId || seen.has(conversationId)) continue
    seen.add(conversationId)
    links.push({
      key: `execution-${event.seq}`,
      taskId: String(value?.task_id || ''),
      conversationId,
      summary: props.executionConversations.find((item) => (
        item.conversation_id === conversationId
      )) || null,
    })
  }
  // global.agent_execution_link 投影有数量上限；持久化的 parent/child 关系
  // 才是完整来源。保留仍在投影中的执行顺序，再补上被压缩掉的直接子会话。
  for (const summary of props.executionConversations) {
    if (seen.has(summary.conversation_id)) continue
    seen.add(summary.conversation_id)
    links.push({
      key: `execution-child-${summary.conversation_id}`,
      taskId: '',
      conversationId: summary.conversation_id,
      summary,
    })
  }
  return links
})

const executionSummaryText = computed(() => {
  const completed = agentExecutionLinks.value.filter((link) => (
    link.summary?.status === 'completed'
  )).length
  const failed = agentExecutionLinks.value.filter((link) => (
    link.summary?.status === 'failed' || link.summary?.status === 'interrupted'
  )).length
  const scope = props.executionConversations.length >= 200
    ? '最近 200 个子 Agent'
    : `${agentExecutionLinks.value.length} 个子 Agent`
  if (failed) return `${scope}，${failed} 个异常`
  if (completed === agentExecutionLinks.value.length) {
    return `${scope}，${props.executionConversations.length >= 200 ? '当前均已完成' : '全部完成'}`
  }
  return `${scope}，${completed} 个完成`
})

const canStartGoal = computed(() => (
  !loadingTask.value && (
    (!activeTaskId.value && !latestProjectedTaskId.value)
  || Boolean(task.value && TERMINAL_STATUSES.has(task.value.status))
  )
))

const canCancelTask = computed(() => Boolean(task.value && [
  'planning',
  'running',
  'needs_input',
  'waiting_publish_confirmation',
].includes(task.value.status)))

const requiredInputsComplete = computed(() => (
  task.value?.pending_inputs.every((item) => isInputComplete(item)) ?? false
))
const canSubmitRequiredInputs = computed(() => (
  requiredInputsComplete.value || Boolean(inputMessage.value.trim())
))

const currentStepText = computed(() => {
  const current = task.value?.current_step_index ?? -1
  const total = task.value?.steps.length || 0
  if (!total) {
    if (task.value?.status === 'completed') return '无需执行业务步骤'
    if (task.value?.status === 'failed') return '未生成可执行计划'
    if (task.value?.status === 'cancelled') return '任务已取消'
    if (task.value?.status === 'needs_input') return '等待规划所需资料'
    return '正在生成执行计划'
  }
  return `步骤 ${Math.min(Math.max(current + 1, 1), total)} / ${total}`
})

watch(() => props.conversationId, (conversationId, previousId) => {
  if (previousId && conversationId !== previousId) {
    resetConversationState()
  }
  if (conversationId) responseConversationId.value = conversationId
})

watch(latestProjectedTaskId, (taskId) => {
  if (!taskId || taskId === activeTaskId.value) return
  activeTaskId.value = taskId
  void loadTaskState(taskId)
}, { immediate: true })

watch(() => task.value?.pending_inputs, (requiredInputs) => {
  const nextValues: Record<string, string | string[]> = {}
  for (const item of requiredInputs || []) {
    const previous = inputValues.value[item.key]
    nextValues[item.key] = previous ?? (
      item.input_type === 'string_list' && item.options.length ? [] : ''
    )
  }
  inputValues.value = nextValues
}, { deep: true })

onBeforeUnmount(() => {
  requestGeneration += 1
  stopPolling()
})

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' ? value as Record<string, unknown> : null
}

function resetConversationState() {
  requestGeneration += 1
  stopPolling()
  task.value = null
  activeTaskId.value = ''
  responseConversationId.value = ''
  recentSnapshotId.value = ''
  goal.value = ''
  inputMessage.value = ''
  inputValues.value = {}
  optimisticMessages.value = []
  busyAction.value = ''
  loadingTask.value = false
  error.value = ''
}

function stopPolling() {
  if (pollTimer !== null) {
    window.clearTimeout(pollTimer)
    pollTimer = null
  }
}

function schedulePoll() {
  stopPolling()
  if (!task.value || !POLLABLE_STATUSES.has(task.value.status)) return
  const taskId = task.value.task_id
  pollTimer = window.setTimeout(() => {
    pollTimer = null
    void loadTaskState(taskId, true)
  }, POLL_INTERVAL_MS)
}

function applyResponse(response: GlobalTaskResponse) {
  task.value = response.task
  activeTaskId.value = response.task_id || response.task.task_id
  responseConversationId.value = response.ai_work_conversation_id
    || response.task.ai_work_conversation_id
    || currentConversationId.value
  if (response.task.draft_query_snapshot_id) {
    recentSnapshotId.value = response.task.draft_query_snapshot_id
  }
  schedulePoll()
  if (responseConversationId.value) {
    emit('refresh-events', responseConversationId.value)
  }
}

async function loadTaskState(taskId: string, silent = false) {
  if (!taskId) return
  const generation = ++requestGeneration
  stopPolling()
  if (!silent) loadingTask.value = true
  try {
    const response = await fetchGlobalTaskState(taskId)
    if (generation !== requestGeneration || activeTaskId.value !== taskId) return
    applyResponse(response)
    error.value = ''
  } catch (cause) {
    if (generation !== requestGeneration) return
    error.value = errorMessage(cause)
    if (task.value && POLLABLE_STATUSES.has(task.value.status)) schedulePoll()
  } finally {
    if (generation === requestGeneration) loadingTask.value = false
  }
}

async function submitGoal() {
  const message = goal.value.trim()
  if (!message || busyAction.value || !canStartGoal.value) return
  error.value = ''
  busyAction.value = 'start'
  const optimistic = addOptimisticMessage(message, '')
  try {
    const response = await startGlobalTask({
      goal: message,
      task_kind: 'global.agent.chat',
      ...(currentConversationId.value
        ? { ai_work_conversation_id: currentConversationId.value }
        : {}),
      ...(recentSnapshotId.value
        ? { draft_query_snapshot_id: recentSnapshotId.value }
        : {}),
    })
    optimistic.taskId = response.task_id
    goal.value = ''
    applyResponse(response)
    emit('conversation-created', {
      conversationId: response.ai_work_conversation_id,
      taskId: response.task_id,
    })
  } catch (cause) {
    optimisticMessages.value = optimisticMessages.value.filter((item) => item !== optimistic)
    error.value = errorMessage(cause)
  } finally {
    busyAction.value = ''
  }
}

async function submitRequiredInputs() {
  if (!task.value || task.value.status !== 'needs_input' || busyAction.value) return
  if (!canSubmitRequiredInputs.value) {
    error.value = '请填写必需资料，或提交补充说明以便重新规划。'
    return
  }
  let normalizedInputs: Record<string, unknown>
  try {
    normalizedInputs = normalizeRequiredInputs(
      task.value.pending_inputs,
      !requiredInputsComplete.value,
    )
  } catch (cause) {
    error.value = errorMessage(cause)
    return
  }
  const message = inputMessage.value.trim()
    || task.value.pending_inputs.map((item) => (
      `${item.label}：${displayInputValue(inputValues.value[item.key])}`
    )).join('；')
  const optimistic = addOptimisticMessage(message, task.value.task_id)
  busyAction.value = 'input'
  error.value = ''
  try {
    const response = await submitGlobalTaskInput({
      task_id: task.value.task_id,
      message,
      inputs: normalizedInputs,
    })
    applyResponse(response)
    inputMessage.value = ''
  } catch (cause) {
    optimisticMessages.value = optimisticMessages.value.filter((item) => item !== optimistic)
    error.value = errorMessage(cause)
  } finally {
    busyAction.value = ''
  }
}

async function confirmPublish() {
  if (!task.value || task.value.status !== 'waiting_publish_confirmation' || busyAction.value) return
  busyAction.value = 'confirm'
  error.value = ''
  try {
    applyResponse(await confirmGlobalTaskPublish(task.value.task_id))
  } catch (cause) {
    error.value = errorMessage(cause)
  } finally {
    busyAction.value = ''
  }
}

async function cancelTask() {
  if (!task.value || !canCancelTask.value || busyAction.value) return
  busyAction.value = 'cancel'
  error.value = ''
  try {
    applyResponse(await cancelGlobalTask(task.value.task_id))
  } catch (cause) {
    error.value = errorMessage(cause)
  } finally {
    busyAction.value = ''
  }
}

function addOptimisticMessage(message: string, taskId: string): ChatMessage {
  const value: ChatMessage = {
    key: `local-${++optimisticSequence}`,
    taskId,
    role: 'user',
    message,
  }
  optimisticMessages.value.push(value)
  return value
}

function errorMessage(cause: unknown): string {
  return cause instanceof Error ? cause.message : String(cause)
}

function statusText(status: GlobalTaskStatus): string {
  return {
    planning: '正在规划',
    running: '正在执行',
    needs_input: '等待补充资料',
    waiting_publish_confirmation: '等待发布确认',
    waiting_publish_result: '等待平台结果',
    completed: '已完成',
    failed: '失败',
    cancelled: '已取消',
  }[status]
}

function statusClass(status: GlobalTaskStatus): string {
  if (status === 'completed') return 'badge-success'
  if (status === 'failed' || status === 'cancelled') return 'badge-danger'
  if (status === 'needs_input' || status === 'waiting_publish_confirmation') {
    return 'bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-200'
  }
  return 'badge-info'
}

function stepStatusText(status: GlobalTaskStepStatus): string {
  return {
    pending: '待执行',
    running: '执行中',
    needs_input: '待补充',
    completed: '已完成',
    failed: '失败',
  }[status]
}

function stepDotClass(status: GlobalTaskStepStatus): string {
  if (status === 'completed') return 'bg-emerald-500'
  if (status === 'failed') return 'bg-rose-500'
  if (status === 'running') return 'bg-sky-500 ring-4 ring-sky-100 dark:ring-sky-500/20'
  if (status === 'needs_input') return 'bg-amber-500 ring-4 ring-amber-100 dark:ring-amber-500/20'
  return 'bg-slate-300 dark:bg-dark-600'
}

function publishSummaryValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '-'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

function inputId(input: GlobalTaskRequiredInput): string {
  return `global-input-${input.key}`
}

function isInputComplete(input: GlobalTaskRequiredInput): boolean {
  const value = inputValues.value[input.key]
  if (input.input_type === 'string_list') {
    if (Array.isArray(value)) return value.some((item) => item.trim())
    return Boolean(String(value || '').trim())
  }
  if (input.input_type === 'json_object') {
    try {
      const parsed = JSON.parse(String(value || ''))
      return Boolean(parsed && typeof parsed === 'object' && !Array.isArray(parsed)
        && Object.keys(parsed).length)
    } catch {
      return false
    }
  }
  return Boolean(String(value || '').trim())
}

function normalizeRequiredInputs(
  requiredInputs: GlobalTaskRequiredInput[],
  allowPartial = false,
): Record<string, unknown> {
  const normalized: Record<string, unknown> = {}
  for (const input of requiredInputs) {
    const value = inputValues.value[input.key]
    if (allowPartial && !isInputComplete(input)) continue
    if (input.input_type === 'json_object') {
      const parsed = JSON.parse(String(value || ''))
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        throw new Error(`${input.label} 必须是 JSON 对象。`)
      }
      normalized[input.key] = parsed
      continue
    }
    if (input.input_type === 'string_list') {
      normalized[input.key] = Array.isArray(value)
        ? value.map((item) => item.trim()).filter(Boolean)
        : String(value || '').split(/[\n,，]/).map((item) => item.trim()).filter(Boolean)
      continue
    }
    normalized[input.key] = String(value || '').trim()
  }
  return normalized
}

function displayInputValue(value: string | string[] | undefined): string {
  return Array.isArray(value) ? value.join('、') : String(value || '')
}

function executionTitle(link: AgentExecutionLink): string {
  const useCaseId = link.summary?.use_case_id || ''
  return {
    'global.task.plan': '任务规划',
    'category.product_match': '类目匹配',
    'category.attribute_fill': '属性补全',
  }[useCaseId] || useCaseId || '内部 Agent 执行'
}

function executionStatusText(link: AgentExecutionLink): string {
  const summary = link.summary
  if (!summary) {
    if (props.executionConversationsState === 'loaded') return '记录不可用'
    if (props.executionConversationsState === 'failed') return '同步失败'
    return '正在同步'
  }
  return {
    running: '进行中',
    waiting_approval: '等待审批',
    completed: '已完成',
    failed: '失败',
    interrupted: '已中断',
  }[summary.status]
}

function executionStatusClass(summary: AiWorkConversationSummary | null): string {
  if (!summary || summary.status === 'running') return 'badge-info'
  if (summary.status === 'completed') return 'badge-success'
  if (summary.status === 'waiting_approval') {
    return 'bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-200'
  }
  return 'badge-danger'
}

function executionTime(link: AgentExecutionLink): string {
  const summary = link.summary
  if (!summary) return link.conversationId
  const createdAt = new Date(summary.created_at)
  const updatedAt = new Date(summary.updated_at)
  const startText = Number.isNaN(createdAt.getTime())
    ? summary.created_at
    : createdAt.toLocaleString()
  if (
    summary.status === 'running'
    || Number.isNaN(createdAt.getTime())
    || Number.isNaN(updatedAt.getTime())
  ) {
    return `开始于 ${startText}`
  }
  const elapsedSeconds = Math.max(0, Math.round(
    (updatedAt.getTime() - createdAt.getTime()) / 1000,
  ))
  if (elapsedSeconds < 60) return `${startText} · 耗时 ${elapsedSeconds || '<1'} 秒`
  const minutes = Math.floor(elapsedSeconds / 60)
  const seconds = elapsedSeconds % 60
  return `${startText} · 耗时 ${minutes} 分 ${seconds} 秒`
}
</script>

<template>
  <section class="mx-auto flex h-full w-full max-w-5xl flex-col" data-testid="global-agent-chat">
    <div class="min-h-0 flex-1 space-y-4 overflow-y-auto pb-5">
      <div
        v-if="!messages.length && !task && !loadingTask"
        class="rounded-2xl border border-dashed border-primary-300 bg-primary-50/60 px-6 py-10 text-center dark:border-primary-500/40 dark:bg-primary-500/10"
      >
        <div class="mx-auto flex h-12 w-12 items-center justify-center rounded-2xl bg-primary-500 text-xl text-white shadow-lg shadow-primary-500/20">
          ✦
        </div>
        <h3 class="mt-4 text-base font-black">告诉全局 Agent 你想完成什么</h3>
        <p class="mx-auto mt-2 max-w-xl text-sm leading-6 text-slate-500 dark:text-accent-300">
          可以查询或整理草稿、准备目标市场资料，并在你明确确认后提交发布。
        </p>
      </div>

      <article
        v-for="message in messages"
        :key="message.key"
        class="flex"
        :class="message.role === 'user' ? 'justify-end' : 'justify-start'"
      >
        <div
          class="max-w-[82%] rounded-2xl px-4 py-3 text-sm leading-6 shadow-sm"
          :class="message.role === 'user'
            ? 'rounded-br-md bg-primary-600 text-white'
            : 'rounded-bl-md border border-slate-200 bg-white text-slate-800 dark:border-dark-700 dark:bg-dark-800 dark:text-accent-100'"
        >
          <p class="mb-1 text-[10px] font-black uppercase tracking-wider opacity-70">
            {{ message.role === 'user' ? '你' : '全局 Agent' }}
          </p>
          <p class="whitespace-pre-wrap break-words">{{ message.message }}</p>
        </div>
      </article>

      <article v-if="loadingTask && !task" class="rounded-2xl border border-slate-200 p-5 text-sm text-slate-500 dark:border-dark-700">
        正在恢复任务状态……
      </article>

      <article
        v-else-if="activeTaskId && !task"
        class="rounded-2xl border border-rose-200 bg-rose-50 p-5 text-sm text-rose-700 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-200"
      >
        <p>当前对话有关联任务，但暂时无法读取任务状态。为避免创建并行任务，发送入口已暂停。</p>
        <button type="button" class="btn btn-outline mt-3 px-3 py-1.5 text-xs" @click="loadTaskState(activeTaskId)">
          重新读取
        </button>
      </article>

      <article v-if="task" class="overflow-hidden rounded-2xl border border-slate-200 bg-white dark:border-dark-700 dark:bg-dark-800/70" data-testid="global-task-state">
        <header class="flex flex-wrap items-start justify-between gap-3 border-b border-slate-200 px-5 py-4 dark:border-dark-700">
          <div class="min-w-0">
            <div class="flex flex-wrap items-center gap-2">
              <h3 class="font-black">任务进度</h3>
              <span class="badge" :class="statusClass(task.status)">{{ statusText(task.status) }}</span>
            </div>
            <p class="mt-2 break-words text-sm text-slate-600 dark:text-accent-200">{{ task.goal }}</p>
            <p v-if="task.plan_explanation" class="mt-2 break-words text-sm text-slate-500 dark:text-accent-300">
              {{ task.plan_explanation }}
            </p>
            <p class="mt-1 text-xs text-slate-400">{{ currentStepText }} · {{ task.task_id }}</p>
          </div>
          <button
            v-if="canCancelTask"
            type="button"
            class="btn btn-outline px-3 py-1.5 text-xs"
            :disabled="Boolean(busyAction)"
            data-testid="cancel-global-task"
            @click="cancelTask"
          >
            {{ busyAction === 'cancel' ? '正在取消…' : '取消任务' }}
          </button>
        </header>

        <ol v-if="task.steps.length" class="divide-y divide-slate-100 px-5 dark:divide-dark-700">
          <li v-for="step in task.steps" :key="step.step_id" class="flex gap-3 py-4">
            <span class="mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full" :class="stepDotClass(step.status)"></span>
            <div class="min-w-0 flex-1">
              <div class="flex flex-wrap items-center justify-between gap-2">
                <p class="text-sm font-bold">{{ step.objective || step.capability }}</p>
                <span class="text-xs font-semibold text-slate-500">{{ stepStatusText(step.status) }}</span>
              </div>
              <p class="mt-1 text-xs text-slate-400">{{ step.capability }}</p>
              <p v-if="step.result_summary" class="mt-2 text-sm text-slate-600 dark:text-accent-200">{{ step.result_summary }}</p>
              <p v-if="step.error_code" class="mt-2 text-xs font-bold text-rose-600">{{ step.error_code }}</p>
            </div>
          </li>
        </ol>
      </article>

      <article
        v-if="task?.status === 'needs_input'"
        class="rounded-2xl border border-amber-200 bg-amber-50 p-5 dark:border-amber-500/30 dark:bg-amber-500/10"
        data-testid="required-input-card"
      >
        <h3 class="font-black text-amber-900 dark:text-amber-100">需要你补充资料</h3>
        <p class="mt-1 text-sm text-amber-700 dark:text-amber-200">补全以下字段后，任务会从当前步骤继续。</p>
        <div class="mt-4 space-y-4">
          <label v-for="input in task.pending_inputs" :key="input.key" class="block" :for="inputId(input)">
            <span class="field-label text-amber-900 dark:text-amber-100">{{ input.label }}</span>
            <span v-if="input.reason" class="mt-1 block text-xs text-amber-700 dark:text-amber-200">{{ input.reason }}</span>
            <select
              v-if="input.input_type === 'string_list' && input.options.length"
              :id="inputId(input)"
              v-model="inputValues[input.key]"
              class="input mt-2 min-h-28"
              multiple
            >
              <option v-for="option in input.options" :key="option" :value="option">{{ option }}</option>
            </select>
            <textarea
              v-else-if="input.input_type === 'json_object'"
              :id="inputId(input)"
              v-model="inputValues[input.key]"
              class="input mt-2 min-h-28 resize-y font-mono text-xs"
              :placeholder="`请输入 JSON 对象，例如 {&quot;字段&quot;: &quot;值&quot;}`"
            ></textarea>
            <textarea
              v-else-if="input.input_type === 'string_list'"
              :id="inputId(input)"
              v-model="inputValues[input.key]"
              class="input mt-2 min-h-20 resize-y"
              :placeholder="`请输入${input.label}，每行或逗号分隔`"
            ></textarea>
            <select
              v-else-if="input.options.length"
              :id="inputId(input)"
              v-model="inputValues[input.key]"
              class="input mt-2"
            >
              <option value="">请选择</option>
              <option v-for="option in input.options" :key="option" :value="option">{{ option }}</option>
            </select>
            <input
              v-else
              :id="inputId(input)"
              v-model="inputValues[input.key]"
              class="input mt-2"
              :placeholder="`请输入${input.label}`"
            />
          </label>
          <label class="block">
            <span class="field-label text-amber-900 dark:text-amber-100">补充说明（可选）</span>
            <textarea v-model="inputMessage" class="input mt-2 min-h-20 resize-y" placeholder="如有额外要求，可在这里说明"></textarea>
          </label>
          <button
            type="button"
            class="btn btn-primary"
            :disabled="Boolean(busyAction) || !canSubmitRequiredInputs"
            data-testid="submit-global-input"
            @click="submitRequiredInputs"
          >
            {{ busyAction === 'input' ? '正在提交…' : '提交资料并继续' }}
          </button>
        </div>
      </article>

      <article
        v-if="task?.status === 'waiting_publish_confirmation'"
        class="rounded-2xl border border-amber-300 bg-amber-50 p-5 dark:border-amber-500/40 dark:bg-amber-500/10"
        data-testid="publish-confirmation-card"
      >
        <h3 class="font-black text-amber-950 dark:text-amber-100">发布前最终确认</h3>
        <p class="mt-2 text-sm leading-6 text-amber-800 dark:text-amber-200">
          确定性校验已经通过。请核对摘要；只有点击下方按钮才会提交到平台，发送文字不会触发发布。
        </p>
        <dl v-if="Object.keys(task.publish_confirmation.summary).length" class="mt-4 grid gap-3 rounded-xl bg-white/70 p-4 sm:grid-cols-2 dark:bg-dark-900/60">
          <div v-for="(value, key) in task.publish_confirmation.summary" :key="key">
            <dt class="text-xs font-bold text-slate-500">{{ key }}</dt>
            <dd class="mt-1 break-words text-sm font-semibold">{{ publishSummaryValue(value) }}</dd>
          </div>
        </dl>
        <div class="mt-4 flex flex-wrap gap-3">
          <button
            type="button"
            class="btn btn-primary"
            :disabled="Boolean(busyAction)"
            data-testid="confirm-global-publish"
            @click="confirmPublish"
          >
            {{ busyAction === 'confirm' ? '正在提交发布…' : '确认发布' }}
          </button>
          <button type="button" class="btn btn-outline" :disabled="Boolean(busyAction)" @click="cancelTask">
            取消任务
          </button>
        </div>
      </article>

      <article
        v-if="task?.status === 'waiting_publish_result'"
        class="rounded-2xl border border-sky-200 bg-sky-50 p-5 dark:border-sky-500/30 dark:bg-sky-500/10"
        data-testid="publish-result-waiting"
      >
        <div class="flex items-center gap-3">
          <span class="h-3 w-3 animate-pulse rounded-full bg-sky-500"></span>
          <h3 class="font-black text-sky-900 dark:text-sky-100">发布已提交，正在等待平台真实终态</h3>
        </div>
        <p class="mt-2 text-sm text-sky-700 dark:text-sky-200">
          {{ task.publish_job_id ? `PublishingBus Job：${task.publish_job_id}` : '正在读取发布任务状态…' }}
        </p>
        <p class="mt-1 text-xs text-sky-600 dark:text-sky-300">页面会定期刷新状态，不会重复提交发布。</p>
      </article>

      <article v-if="task?.status === 'failed'" class="rounded-2xl border border-rose-200 bg-rose-50 p-5 text-rose-800 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-200">
        <h3 class="font-black">任务执行失败</h3>
        <p class="mt-2 whitespace-pre-wrap text-sm">{{ task.error_message || '任务未能完成。' }}</p>
        <p v-if="task.error_code" class="mt-2 text-xs font-bold">{{ task.error_code }}</p>
      </article>

      <details
        v-if="agentExecutionLinks.length"
        class="overflow-hidden rounded-2xl border border-slate-200 bg-white dark:border-dark-700 dark:bg-dark-800/70"
        data-testid="agent-execution-details"
      >
        <summary class="flex cursor-pointer items-center justify-between gap-3 px-5 py-4">
          <span class="font-black">执行详情</span>
          <span class="text-xs font-semibold text-slate-500 dark:text-accent-300">
            {{ executionSummaryText }}
          </span>
        </summary>
        <ul class="divide-y divide-slate-100 border-t border-slate-200 dark:divide-dark-700 dark:border-dark-700">
          <li
            v-if="props.executionConversations.length >= 200"
            class="bg-amber-50 px-5 py-3 text-xs text-amber-800 dark:bg-amber-500/10 dark:text-amber-200"
          >
            子会话较多，仅显示最近 200 条执行记录。
          </li>
          <li v-for="link in agentExecutionLinks" :key="link.key">
            <button
              type="button"
              class="w-full px-5 py-4 text-left transition hover:bg-slate-50 dark:hover:bg-dark-800"
              :data-testid="`open-agent-execution-${link.conversationId}`"
              @click="emit('open-execution', link.conversationId)"
            >
              <div class="flex flex-wrap items-center justify-between gap-2">
                <div class="flex min-w-0 items-center gap-2">
                  <span class="truncate text-sm font-bold">{{ executionTitle(link) }}</span>
                  <span class="badge-muted">内部执行</span>
                </div>
                <span class="badge" :class="executionStatusClass(link.summary)">
                  {{ executionStatusText(link) }}
                </span>
              </div>
              <p class="mt-2 truncate text-xs text-slate-500 dark:text-accent-300">
                {{ link.summary?.provider_id || 'Agent' }} ·
                {{ link.summary?.model || link.summary?.model_id || link.conversationId }}
              </p>
              <p class="mt-1 text-[11px] text-slate-400">{{ executionTime(link) }}</p>
            </button>
          </li>
        </ul>
      </details>

      <div v-if="error" class="rounded-xl bg-rose-50 p-4 text-sm text-rose-700 ring-1 ring-rose-200" role="alert">
        {{ error }}
      </div>
    </div>

    <form
      v-if="canStartGoal"
      class="shrink-0 border-t border-slate-200 bg-white pt-4 dark:border-dark-700 dark:bg-dark-900"
      data-testid="global-goal-composer"
      @submit.prevent="submitGoal"
    >
      <label class="sr-only" for="global-agent-goal">给全局 Agent 的目标</label>
      <div class="flex items-end gap-3">
        <textarea
          id="global-agent-goal"
          v-model="goal"
          class="input min-h-24 flex-1 resize-y"
          placeholder="例如：把第二个草稿准备到 Ozon，并在发布前让我确认"
          :disabled="Boolean(busyAction)"
          @keydown.enter.exact.prevent="submitGoal"
        ></textarea>
        <button type="submit" class="btn btn-primary mb-0.5" :disabled="!goal.trim() || Boolean(busyAction)" data-testid="send-global-goal">
          {{ busyAction === 'start' ? '正在发送…' : '发送' }}
        </button>
      </div>
      <p class="mt-2 text-xs text-slate-400">
        Enter 发送，Shift + Enter 换行。发布操作始终需要单独点击确认按钮。
      </p>
    </form>
  </section>
</template>
