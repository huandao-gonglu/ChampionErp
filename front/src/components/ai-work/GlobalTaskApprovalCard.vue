<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  approveGlobalTask,
  cancelGlobalTask,
  fetchGlobalTask,
  rejectGlobalTask,
  submitGlobalTaskInput,
} from '@/api/globalTasks'
import type {
  GlobalTaskExecutionProgress,
  GlobalTaskInputType,
  GlobalTaskRequiredInput,
  GlobalTaskState,
  GlobalTaskStatus,
} from '@/types/aiWork'

const props = withDefaults(defineProps<{
  taskId: string
  enabled?: boolean
}>(), {
  enabled: false,
})

const task = ref<GlobalTaskState | null>(null)
const progress = ref<GlobalTaskExecutionProgress | null>(null)
const busyAction = ref<'approve' | 'reject' | 'refresh' | 'input' | 'cancel' | ''>('')
const actionError = ref('')
const rejectionReason = ref('')
const inputValues = ref<Record<string, string>>({})
const multiSelectValues = ref<Record<string, string[]>>({})

let pollTimer: ReturnType<typeof setInterval> | null = null
const POLL_INTERVAL_MS = 4000
// 报告 A-10：任务卡的只读 GET 与写操作响应可能反序完成。loadGeneration 单调
// 递增，响应回来时只有最新一次请求（且 taskId 未切换）的结果允许写入，旧响应
// 不得覆盖较新状态；写操作完成时也递增代次，使在途的旧只读响应作废。
let loadGeneration = 0

// -- 本地计时（进度计划 §8.3）----------------------------------------------
// 服务端以 observed_at + 耗时秒数为基准；前端记录本地接收时刻，每秒用
// Date.now() 差值刷新显示，下一次 GET 重新校准。基于时间戳差值而非累计
// tick，浏览器后台节流不会造成计时漂移。
const progressReceivedAt = ref(0)
const tickNow = ref(Date.now())
let tickTimer: ReturnType<typeof setInterval> | null = null

const pendingApproval = computed(() => task.value?.pending_approval || null)
const pendingInputs = computed(() => task.value?.pending_inputs || [])
const approvalSummary = computed(() => String(pendingApproval.value?.payload?.summary || '').trim())
const approvalPayload = computed(() => pendingApproval.value?.payload?.canonical_payload || {})
const isTerminal = computed(() => {
  const status = task.value?.status
  return status === 'completed' || status === 'failed' || status === 'cancelled'
})

const activeJob = computed(() => progress.value?.active_job || null)
const currentStep = computed(() => progress.value?.current_step || null)
const activities = computed(() => progress.value?.activities || [])

const localElapsedSeconds = computed(() => {
  if (!progress.value) return 0
  return Math.max(0, Math.floor((tickNow.value - progressReceivedAt.value) / 1000))
})

const displayTaskElapsed = computed(() => {
  const base = progress.value?.task_elapsed_seconds ?? 0
  return isTerminal.value ? base : base + localElapsedSeconds.value
})

const displayJobElapsed = computed(() => {
  const job = activeJob.value
  if (!job) return 0
  return isTerminal.value ? job.elapsed_seconds : job.elapsed_seconds + localElapsedSeconds.value
})

const currentStepLine = computed(() => {
  const step = currentStep.value
  if (!step || !step.total) return ''
  return `第 ${step.ordinal}/${step.total} 步：${step.label || step.capability_name}`
})

const jobStatusLine = computed(() => {
  const job = activeJob.value
  if (!job) return ''
  const label = job.stage_label || job.summary || '后台任务执行中'
  return `${label} · 已耗时 ${displayJobElapsed.value}s`
})

const nextCheckCountdown = computed(() => {
  const job = activeJob.value
  if (!job || !job.next_check_at || !progress.value) return null
  const nextMs = Date.parse(job.next_check_at)
  const observedMs = Date.parse(progress.value.observed_at)
  if (Number.isNaN(nextMs) || Number.isNaN(observedMs)) return null
  // 以服务端 observed_at 为锚点推算当前服务端时间，再求剩余秒数。
  const currentServerMs = observedMs + (tickNow.value - progressReceivedAt.value)
  return Math.max(0, Math.floor((nextMs - currentServerMs) / 1000))
})

const externalStatusLine = computed(() => {
  const job = activeJob.value
  if (!job) return ''
  const parts: string[] = []
  if (job.last_external_status) parts.push(`最近状态：${job.last_external_status}`)
  if (job.retry_count != null && job.retry_count > 0) parts.push(`已检查 ${job.retry_count} 次`)
  if (nextCheckCountdown.value != null) parts.push(`${nextCheckCountdown.value}s 后再次检查`)
  return parts.join(' · ')
})

// aria-live 只播报阶段/状态变化，不包含每秒跳动的耗时与倒计时。
const stageAnnouncement = computed(() => {
  const job = activeJob.value
  const parts = [currentStepLine.value]
  if (job) parts.push(job.stage_label || job.summary)
  if (job?.last_external_status) parts.push(`最近状态：${job.last_external_status}`)
  return parts.filter(Boolean).join('；')
})

const technicalDetail = computed(() => {
  if (!progress.value) return ''
  const job = activeJob.value
  const lines: string[] = []
  const step = currentStep.value
  if (step) lines.push(`capability: ${step.capability_name}`)
  if (job) {
    lines.push(`job_id: ${job.job_id}`)
    if (job.stage_code) lines.push(`stage: ${job.stage_code}`)
    if (job.attempt != null) lines.push(`attempt: ${job.attempt}`)
  }
  if (activities.value.length) {
    lines.push(`activities: ${activities.value.map((item) => item.code).join(', ')}`)
  }
  return lines.join('\n')
})

function activityIcon(status: string): string {
  if (status === 'completed') return '✓'
  if (status === 'running' || status === 'retrying' || status === 'waiting') return '●'
  return '○'
}

function activityIconClass(status: string): string {
  if (status === 'completed') return 'text-emerald-500'
  if (status === 'failed') return 'text-rose-500'
  if (status === 'running' || status === 'retrying' || status === 'waiting') return 'text-sky-500'
  return 'text-slate-300 dark:text-dark-600'
}

function commitResponse(response: { task: GlobalTaskState; execution_progress?: GlobalTaskExecutionProgress | null }): void {
  task.value = response.task
  progress.value = response.execution_progress || null
  progressReceivedAt.value = Date.now()
  tickNow.value = Date.now()
  syncTicker()
}

function syncTicker(): void {
  const shouldTick = Boolean(progress.value) && !isTerminal.value
  if (shouldTick && tickTimer === null) {
    tickTimer = setInterval(() => {
      tickNow.value = Date.now()
    }, 1000)
  } else if (!shouldTick && tickTimer !== null) {
    clearInterval(tickTimer)
    tickTimer = null
  }
}

function handleVisibilityChange(): void {
  // 标签页恢复时直接按时间差校准，不依赖后台被节流的 tick。
  if (!document.hidden) tickNow.value = Date.now()
}

const statusLabel = computed(() => {
  const labels: Record<GlobalTaskStatus, string> = {
    running: '执行中',
    needs_input: '等待补充资料',
    pending_approval: '等待你的审批',
    in_progress: '后台任务执行中',
    completed: '已完成',
    failed: '执行失败',
    cancelled: '已取消',
  }
  const status = task.value?.status
  return status ? labels[status] || status : ''
})

const completedSteps = computed(() => (
  task.value?.steps.filter((step) => step.status === 'completed').length || 0
))

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error || '任务操作失败。')
}

function normalizedInputType(item: GlobalTaskRequiredInput): GlobalTaskInputType {
  const type = item.input_type
  if (type === 'select' || type === 'multi_select' || type === 'json_object' || type === 'string_list') return type
  return 'text'
}

function selectedMultiSelectValues(item: GlobalTaskRequiredInput): string[] {
  const selected = new Set(multiSelectValues.value[item.key] || [])
  return (item.options || []).map((option) => option.value).filter((value) => selected.has(value))
}

function isSalesTargetMultiSelect(item: GlobalTaskRequiredInput): boolean {
  return item.key === 'sales_target' && normalizedInputType(item) === 'multi_select'
}

function salesTargetSiteId(value: string): string {
  if (value.split(':').length !== 2) return value
  return value.split(':', 1)[0]?.trim().toUpperCase() || value
}

function multiSelectGroupKey(item: GlobalTaskRequiredInput, value: string): string {
  return isSalesTargetMultiSelect(item) ? salesTargetSiteId(value) : value
}

function multiSelectGroupCount(item: GlobalTaskRequiredInput): number {
  return new Set((item.options || []).map((option) => multiSelectGroupKey(item, option.value))).size
}

function multiSelectSummary(item: GlobalTaskRequiredInput): string {
  const selectedCount = selectedMultiSelectValues(item).length
  return isSalesTargetMultiSelect(item)
    ? `已选 ${selectedCount} 个市场`
    : `已选 ${selectedCount} / ${(item.options || []).length}`
}

function setMultiSelectValues(item: GlobalTaskRequiredInput, values: string[]): void {
  const allowed = new Set((item.options || []).map((option) => option.value))
  multiSelectValues.value[item.key] = Array.from(new Set(values)).filter((value) => allowed.has(value))
  actionError.value = ''
}

function toggleMultiSelectOption(item: GlobalTaskRequiredInput, value: string, checked: boolean): void {
  const selected = new Set(selectedMultiSelectValues(item))
  if (checked) {
    if (isSalesTargetMultiSelect(item)) {
      const groupKey = multiSelectGroupKey(item, value)
      ;(item.options || [])
        .filter((option) => multiSelectGroupKey(item, option.value) === groupKey)
        .forEach((option) => selected.delete(option.value))
    }
    selected.add(value)
  }
  else selected.delete(value)
  // 始终按服务端选项顺序提交，避免点击顺序改变任务输入摘要与审批摘要。
  setMultiSelectValues(
    item,
    (item.options || []).map((option) => option.value).filter((optionValue) => selected.has(optionValue)),
  )
}

function selectAllMultiSelectOptions(item: GlobalTaskRequiredInput): void {
  const options = item.options || []
  if (!isSalesTargetMultiSelect(item)) {
    setMultiSelectValues(item, options.map((option) => option.value))
    return
  }

  const current = new Set(selectedMultiSelectValues(item))
  const selectedBySite = new Map<string, string>()
  options.forEach((option) => {
    if (current.has(option.value)) selectedBySite.set(multiSelectGroupKey(item, option.value), option.value)
  })
  options.forEach((option) => {
    const groupKey = multiSelectGroupKey(item, option.value)
    if (selectedBySite.has(groupKey)) return
    const [, logisticType = ''] = option.value.split(':', 2)
    if (logisticType.trim().toLowerCase() === 'remote') selectedBySite.set(groupKey, option.value)
  })
  options.forEach((option) => {
    const groupKey = multiSelectGroupKey(item, option.value)
    if (!selectedBySite.has(groupKey)) selectedBySite.set(groupKey, option.value)
  })
  const selected = new Set(selectedBySite.values())
  setMultiSelectValues(item, options.map((option) => option.value).filter((value) => selected.has(value)))
}

function clearMultiSelectOptions(item: GlobalTaskRequiredInput): void {
  setMultiSelectValues(item, [])
}

/** 按 input_type 校验并序列化用户填写的待补字段。

报告 A-08：后端契约支持 text/select/multi_select/json_object/string_list；旧实现把
所有类型都按字符串提交，真实列表、对象和选项输入无法使用。这里按类型序列化：
select 必须是给定选项之一，multi_select 提交稳定值数组，json_object 必须能解析成
JSON 对象，string_list 按换行/逗号拆成字符串数组。 */
function serializeInputValue(
  item: GlobalTaskRequiredInput,
  raw: string | string[],
): { ok: true; value: unknown } | { ok: false; error: string } {
  const type = normalizedInputType(item)
  if (type === 'multi_select') {
    const selected = Array.isArray(raw)
      ? Array.from(new Set(raw.map((value) => value.trim()).filter(Boolean)))
      : []
    if (!selected.length) return { ok: false, error: `请至少选择一项${item.label}。` }
    const allowed = new Set((item.options || []).map((option) => option.value))
    if (allowed.size && selected.some((value) => !allowed.has(value))) {
      return { ok: false, error: `${item.label}必须从给定选项中选择。` }
    }
    return { ok: true, value: selected }
  }
  const textRaw = Array.isArray(raw) ? '' : raw
  const trimmed = textRaw.trim()
  if (type === 'select') {
    if (!trimmed) return { ok: false, error: `请选择${item.label}。` }
    const options = item.options || []
    if (options.length && !options.some((option) => option.value === trimmed)) {
      return { ok: false, error: `${item.label}必须从给定选项中选择。` }
    }
    return { ok: true, value: trimmed }
  }
  if (type === 'json_object') {
    if (!trimmed) return { ok: false, error: `请填写${item.label}的 JSON 对象。` }
    try {
      const parsed: unknown = JSON.parse(trimmed)
      if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
        return { ok: false, error: `${item.label}必须是 JSON 对象。` }
      }
      return { ok: true, value: parsed }
    } catch {
      return { ok: false, error: `${item.label}不是合法 JSON。` }
    }
  }
  if (type === 'string_list') {
    const items = trimmed
      .split(/[\n,，]/)
      .map((entry) => entry.trim())
      .filter(Boolean)
    if (!items.length) return { ok: false, error: `请填写${item.label}。` }
    return { ok: true, value: items }
  }
  if (!trimmed) return { ok: false, error: `请填写${item.label}。` }
  return { ok: true, value: trimmed }
}

async function loadTask(showBusy = true): Promise<void> {
  if (!props.taskId) return
  // 报告 A-10：记录本次读取的代次与 taskId 快照；响应回来时若已被更新的
  // 请求/写操作取代，或 taskId 已切换，则丢弃，旧响应不得覆盖较新状态。
  const generation = ++loadGeneration
  const requestedTaskId = props.taskId
  if (showBusy) busyAction.value = 'refresh'
  actionError.value = ''
  try {
    // 纯读 GET：任务推进只由后台 worker 完成，前端不触发任何写刷新。
    const response = await fetchGlobalTask(requestedTaskId)
    if (generation !== loadGeneration || requestedTaskId !== props.taskId) return
    commitResponse(response)
  } catch (error) {
    if (generation !== loadGeneration || requestedTaskId !== props.taskId) return
    if (showBusy) actionError.value = errorMessage(error)
  } finally {
    if (generation === loadGeneration && showBusy) busyAction.value = ''
  }
}

function startPolling(): void {
  stopPolling()
  if (!props.enabled) return
  pollTimer = setInterval(() => {
    if (busyAction.value || isTerminal.value) {
      if (isTerminal.value) stopPolling()
      return
    }
    void loadTask(false)
  }, POLL_INTERVAL_MS)
}

function stopPolling(): void {
  if (pollTimer !== null) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

async function approve(): Promise<void> {
  const current = task.value
  const approval = pendingApproval.value
  if (!current || !approval || busyAction.value) return
  const summary = approvalSummary.value || `任务 ${current.task_id} 的当前步骤`
  if (!window.confirm(`确认执行以下高风险操作？\n\n${summary}`)) return
  // 报告 A-10：写请求在途期间 taskId 可能切换；响应回来必须校验请求时的
  // taskId，任务 A 的慢写响应不得覆盖已切换到的任务 B。
  const requestedTaskId = current.task_id
  busyAction.value = 'approve'
  actionError.value = ''
  try {
    const response = await approveGlobalTask(requestedTaskId, approval.step_id)
    // 报告 A-10：先核对请求时的 taskId，再动 generation/状态——任务 A 的旧
    // 写响应不得作废任务 B 的在途读取，也不得覆盖 B 的状态。
    if (requestedTaskId !== props.taskId) return
    // 写响应是较新事实，递增代次使在途旧只读响应作废。
    loadGeneration += 1
    commitResponse(response)
  } catch (error) {
    if (requestedTaskId !== props.taskId) return
    actionError.value = errorMessage(error)
  } finally {
    if (requestedTaskId === props.taskId) busyAction.value = ''
  }
}

async function reject(): Promise<void> {
  const current = task.value
  const approval = pendingApproval.value
  const reason = rejectionReason.value.trim()
  if (!current || !approval || !reason || busyAction.value) return
  const requestedTaskId = current.task_id
  busyAction.value = 'reject'
  actionError.value = ''
  try {
    const response = await rejectGlobalTask(requestedTaskId, approval.step_id, reason)
    if (requestedTaskId !== props.taskId) return
    loadGeneration += 1
    commitResponse(response)
    rejectionReason.value = ''
  } catch (error) {
    if (requestedTaskId !== props.taskId) return
    actionError.value = errorMessage(error)
  } finally {
    if (requestedTaskId === props.taskId) busyAction.value = ''
  }
}

async function submitInput(): Promise<void> {
  const current = task.value
  if (!current || busyAction.value) return
  const args: Record<string, unknown> = {}
  for (const item of pendingInputs.value) {
    const inputType = normalizedInputType(item)
    const raw = inputType === 'multi_select'
      ? selectedMultiSelectValues(item)
      : String(inputValues.value[item.key] || '')
    if (Array.isArray(raw) ? !raw.length : !raw.trim()) continue
    // 报告 A-08：按 input_type 校验并序列化；非法 JSON / 选项外取值在提交前
    // 就地拦截，不再把非文本类型硬转成字符串发给后端。
    const serialized = serializeInputValue(item, raw)
    if (!serialized.ok) {
      actionError.value = serialized.error
      return
    }
    args[item.key] = serialized.value
  }
  if (!Object.keys(args).length) {
    actionError.value = '请至少填写一个补充字段。'
    return
  }
  const requestedTaskId = current.task_id
  busyAction.value = 'input'
  actionError.value = ''
  try {
    const response = await submitGlobalTaskInput(requestedTaskId, args)
    if (requestedTaskId !== props.taskId) return
    loadGeneration += 1
    commitResponse(response)
    inputValues.value = {}
    multiSelectValues.value = {}
  } catch (error) {
    if (requestedTaskId !== props.taskId) return
    actionError.value = errorMessage(error)
  } finally {
    if (requestedTaskId === props.taskId) busyAction.value = ''
  }
}

async function cancelTask(): Promise<void> {
  const current = task.value
  if (!current || busyAction.value) return
  if (!window.confirm('确认取消该任务？已执行的步骤不会被撤销。')) return
  const requestedTaskId = current.task_id
  busyAction.value = 'cancel'
  actionError.value = ''
  try {
    const response = await cancelGlobalTask(requestedTaskId)
    if (requestedTaskId !== props.taskId) return
    loadGeneration += 1
    commitResponse(response)
  } catch (error) {
    if (requestedTaskId !== props.taskId) return
    actionError.value = errorMessage(error)
  } finally {
    if (requestedTaskId === props.taskId) busyAction.value = ''
  }
}

watch(() => props.taskId, () => {
  // 报告 A-10：taskId 切换时递增代次，旧任务的在途慢响应不得写入新任务卡；
  // 同时清除旧任务的 busy 状态与进度视图，新任务的按钮不再被旧在途操作禁用。
  loadGeneration += 1
  busyAction.value = ''
  task.value = null
  progress.value = null
  actionError.value = ''
  inputValues.value = {}
  multiSelectValues.value = {}
  syncTicker()
  void loadTask(false)
})

watch(isTerminal, (terminal) => {
  if (terminal) {
    stopPolling()
    // 终态冻结耗时：停止本地计时。
    syncTicker()
  } else {
    startPolling()
  }
})

onMounted(() => {
  document.addEventListener('visibilitychange', handleVisibilityChange)
  if (!props.taskId) return
  // 只读挂载也先做一次纯 GET 读取以展示状态；轮询仅在可操作入口启用。
  void loadTask(false)
  if (props.enabled) startPolling()
})

onBeforeUnmount(() => {
  document.removeEventListener('visibilitychange', handleVisibilityChange)
  stopPolling()
  if (tickTimer !== null) {
    clearInterval(tickTimer)
    tickTimer = null
  }
})
</script>

<template>
  <section
    v-if="task"
    class="mt-3 rounded-lg border border-slate-200 bg-white p-3 dark:border-dark-600 dark:bg-dark-950"
    data-testid="global-task-card"
  >
    <div class="flex flex-wrap items-center justify-between gap-2">
      <div>
        <p class="text-xs font-black text-slate-700 dark:text-accent-100">全局任务</p>
        <p class="mt-0.5 break-all font-mono text-[10px] text-slate-400">{{ task.task_id }}</p>
      </div>
      <span class="rounded-full bg-sky-50 px-2 py-1 text-[10px] font-bold text-sky-700 ring-1 ring-sky-200 dark:bg-sky-500/10 dark:text-sky-200 dark:ring-sky-500/30">
        {{ statusLabel }}
      </span>
    </div>

    <p v-if="task.goal" class="mt-2 text-xs font-semibold text-slate-700 dark:text-accent-100">
      {{ task.goal }}
    </p>
    <p class="mt-1 text-[11px] text-slate-500 dark:text-accent-300">
      步骤 {{ completedSteps }}/{{ task.steps.length }}
      <span v-if="progress"> · 已耗时 {{ displayTaskElapsed }}s</span>
      <span v-if="task.assistant_message"> · {{ task.assistant_message }}</span>
    </p>

    <!-- 执行进度（进度计划 §8.2）：顶层步骤与 Job 内部活动分层展示；
         aria-live 只播报阶段/状态变化，不播报每秒耗时。 -->
    <div v-if="progress" class="mt-2" data-testid="global-task-progress">
      <p aria-live="polite" class="sr-only">{{ stageAnnouncement }}</p>
      <p
        v-if="currentStepLine"
        class="text-[11px] font-semibold text-slate-600 dark:text-accent-200"
        data-testid="global-task-current-step"
      >
        {{ currentStepLine }}
      </p>
      <p
        v-if="jobStatusLine"
        class="mt-0.5 text-[11px] text-slate-500 dark:text-accent-300"
        data-testid="global-task-job-line"
      >
        {{ jobStatusLine }}
      </p>
      <p
        v-if="externalStatusLine"
        class="mt-0.5 text-[11px] text-slate-500 dark:text-accent-300"
        data-testid="global-task-external-line"
      >
        {{ externalStatusLine }}
      </p>
      <ul
        v-if="activities.length"
        class="mt-1.5 space-y-0.5"
        data-testid="global-task-activities"
      >
        <li
          v-for="activity in activities"
          :key="activity.code"
          class="flex items-center gap-1.5 text-[11px]"
        >
          <span class="w-3 text-center font-bold" :class="activityIconClass(activity.status)">
            {{ activityIcon(activity.status) }}
          </span>
          <span
            :class="activity.status === 'completed'
              ? 'text-slate-400 dark:text-accent-300'
              : 'text-slate-600 dark:text-accent-200'"
          >
            {{ activity.label || activity.code }}
          </span>
        </li>
      </ul>
      <details v-if="technicalDetail" class="mt-1.5">
        <summary class="cursor-pointer text-[10px] text-slate-400 dark:text-accent-300">
          技术详情
        </summary>
        <pre class="mt-1 overflow-auto whitespace-pre-wrap break-words rounded bg-slate-50 p-2 font-mono text-[10px] leading-4 text-slate-500 dark:bg-dark-950 dark:text-accent-300">{{ technicalDetail }}</pre>
      </details>
    </div>

    <!-- 待补资料 -->
    <div
      v-if="task.status === 'needs_input' && pendingInputs.length"
      class="mt-3 rounded-lg border border-sky-200 bg-sky-50 p-3 dark:border-sky-500/30 dark:bg-sky-500/10"
      data-testid="global-task-input"
    >
      <p class="text-xs font-black text-sky-900 dark:text-sky-100">需要补充资料</p>
      <div v-for="item in pendingInputs" :key="item.key" class="mt-2">
        <label class="text-[11px] font-bold text-sky-800 dark:text-sky-200" :for="`task-input-${item.key}`">
          {{ item.label }}
        </label>
        <p v-if="item.reason" class="text-[10px] text-sky-600 dark:text-sky-300">{{ item.reason }}</p>
        <!-- 报告 A-08：按 input_type 渲染控件；select 用单选下拉，multi_select
             用复选列表，json_object/string_list 用多行文本，text 用单行输入。 -->
        <div
          v-if="normalizedInputType(item) === 'multi_select'"
          class="mt-1 rounded-lg border border-sky-200 bg-white p-2 dark:border-sky-500/30 dark:bg-dark-950/60"
          :data-testid="`global-task-input-${item.key}`"
        >
          <div class="flex flex-wrap items-center justify-between gap-2 border-b border-sky-100 pb-2 dark:border-sky-500/20">
            <span class="text-[11px] font-semibold text-sky-700 dark:text-sky-200">
              {{ multiSelectSummary(item) }}
            </span>
            <div class="flex gap-1.5">
              <button
                type="button"
                class="btn btn-outline px-2 py-1 text-[11px]"
                :data-testid="`global-task-input-${item.key}-select-all`"
                :disabled="Boolean(busyAction) || !(item.options || []).length || selectedMultiSelectValues(item).length === multiSelectGroupCount(item)"
                @click="selectAllMultiSelectOptions(item)"
              >
                {{ isSalesTargetMultiSelect(item) ? '全选市场' : '全选' }}
              </button>
              <button
                type="button"
                class="btn btn-outline px-2 py-1 text-[11px]"
                :data-testid="`global-task-input-${item.key}-clear`"
                :disabled="Boolean(busyAction) || !selectedMultiSelectValues(item).length"
                @click="clearMultiSelectOptions(item)"
              >
                清空
              </button>
            </div>
          </div>
          <div class="mt-2 grid max-h-48 gap-1 overflow-y-auto sm:grid-cols-2">
            <label
              v-for="option in item.options || []"
              :key="option.value"
              class="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-xs text-sky-900 transition hover:bg-sky-50 dark:text-sky-100 dark:hover:bg-sky-500/10"
            >
              <input
                type="checkbox"
                class="size-4 rounded border-sky-300 text-primary-600"
                :value="option.value"
                :checked="selectedMultiSelectValues(item).includes(option.value)"
                :disabled="Boolean(busyAction)"
                :data-testid="`global-task-input-${item.key}-option`"
                @change="toggleMultiSelectOption(item, option.value, ($event.target as HTMLInputElement).checked)"
              />
              <span class="min-w-0 break-words">{{ option.label }}</span>
            </label>
            <p v-if="!(item.options || []).length" class="px-2 py-1.5 text-[11px] text-rose-600 dark:text-rose-200">
              当前没有可选项。
            </p>
          </div>
        </div>
        <select
          v-else-if="normalizedInputType(item) === 'select'"
          :id="`task-input-${item.key}`"
          v-model="inputValues[item.key]"
          class="input mt-1 text-xs"
          :data-testid="`global-task-input-${item.key}`"
        >
          <option disabled value="">请选择</option>
          <option
            v-for="option in item.options || []"
            :key="option.value"
            :value="option.value"
          >
            {{ option.label }}
          </option>
        </select>
        <textarea
          v-else-if="normalizedInputType(item) === 'json_object'"
          :id="`task-input-${item.key}`"
          v-model="inputValues[item.key]"
          class="input mt-1 font-mono text-xs"
          rows="3"
          placeholder="{ key: value }"
          :data-testid="`global-task-input-${item.key}`"
        ></textarea>
        <textarea
          v-else-if="normalizedInputType(item) === 'string_list'"
          :id="`task-input-${item.key}`"
          v-model="inputValues[item.key]"
          class="input mt-1 text-xs"
          rows="3"
          placeholder="每行一个，或用逗号分隔"
          :data-testid="`global-task-input-${item.key}`"
        ></textarea>
        <input
          v-else
          :id="`task-input-${item.key}`"
          v-model="inputValues[item.key]"
          class="input mt-1 text-xs"
          :data-testid="`global-task-input-${item.key}`"
        />
      </div>
      <template v-if="enabled">
        <button
          type="button"
          class="btn btn-primary mt-3 px-3 py-1.5 text-xs"
          data-testid="global-task-input-submit"
          :disabled="Boolean(busyAction)"
          @click="submitInput"
        >
          {{ busyAction === 'input' ? '正在提交…' : '提交补充资料' }}
        </button>
      </template>
      <p v-else class="mt-2 text-[11px] text-sky-700 dark:text-sky-200">
        只读消息不能补充资料；请切换到活动的全局 AI 对话。
      </p>
    </div>

    <!-- 待审批 -->
    <div
      v-if="task.status === 'pending_approval' && pendingApproval"
      class="mt-3 rounded-lg border border-amber-200 bg-amber-50 p-3 dark:border-amber-500/30 dark:bg-amber-500/10"
      data-testid="global-task-approval"
    >
      <p class="text-xs font-black text-amber-900 dark:text-amber-100">请确认高风险操作</p>
      <p class="mt-1 whitespace-pre-wrap break-words text-xs leading-5 text-amber-800 dark:text-amber-100">
        {{ approvalSummary || '服务端已生成审批快照，请核对参数后确认。' }}
      </p>
      <details v-if="Object.keys(approvalPayload).length" class="mt-2">
        <summary class="cursor-pointer text-[11px] font-bold text-amber-700 dark:text-amber-200">查看冻结参数</summary>
        <pre class="mt-1 overflow-auto whitespace-pre-wrap break-words rounded bg-white/80 p-2 font-mono text-[10px] leading-4 text-slate-700 dark:bg-dark-950 dark:text-accent-200">{{ JSON.stringify(approvalPayload, null, 2) }}</pre>
      </details>

      <template v-if="enabled">
        <div class="mt-3 flex flex-wrap gap-2">
          <button
            type="button"
            class="btn btn-primary px-3 py-1.5 text-xs"
            data-testid="global-task-approve"
            :disabled="Boolean(busyAction)"
            @click="approve"
          >
            {{ busyAction === 'approve' ? '正在批准…' : '确认执行' }}
          </button>
          <button
            type="button"
            class="btn btn-outline px-3 py-1.5 text-xs"
            data-testid="global-task-reject"
            :disabled="Boolean(busyAction) || !rejectionReason.trim()"
            @click="reject"
          >
            {{ busyAction === 'reject' ? '正在拒绝…' : '拒绝任务' }}
          </button>
        </div>
        <input
          v-model="rejectionReason"
          class="input mt-2 text-xs"
          maxlength="2000"
          placeholder="如需拒绝，请填写原因"
          data-testid="global-task-reject-reason"
        />
      </template>
      <p v-else class="mt-2 text-[11px] text-amber-700 dark:text-amber-200">
        只读消息不能审批；请切换到活动的全局 AI 对话。
      </p>
    </div>

    <p v-if="task.status === 'failed'" class="mt-2 text-xs text-rose-600 dark:text-rose-300">
      {{ task.error_message || task.error_code || '任务执行失败。' }}
    </p>
    <p v-if="actionError" role="alert" class="mt-2 text-xs text-rose-600 dark:text-rose-300" data-testid="global-task-action-error">
      {{ actionError }}
    </p>

    <div v-if="enabled && !isTerminal" class="mt-3 flex flex-wrap gap-2">
      <button
        type="button"
        class="btn btn-outline px-3 py-1.5 text-xs"
        data-testid="global-task-refresh"
        :disabled="Boolean(busyAction)"
        @click="loadTask()"
      >
        {{ busyAction === 'refresh' ? '正在刷新…' : '刷新任务状态' }}
      </button>
      <button
        v-if="task.status !== 'in_progress'"
        type="button"
        class="btn btn-outline px-3 py-1.5 text-xs text-rose-600 dark:text-rose-300"
        data-testid="global-task-cancel"
        :disabled="Boolean(busyAction)"
        @click="cancelTask"
      >
        {{ busyAction === 'cancel' ? '正在取消…' : '取消任务' }}
      </button>
    </div>
  </section>
</template>
