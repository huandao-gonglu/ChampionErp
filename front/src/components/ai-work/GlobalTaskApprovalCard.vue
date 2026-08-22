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
const busyAction = ref<'approve' | 'reject' | 'refresh' | 'input' | 'cancel' | ''>('')
const actionError = ref('')
const rejectionReason = ref('')
const inputValues = ref<Record<string, string>>({})

let pollTimer: ReturnType<typeof setInterval> | null = null
const POLL_INTERVAL_MS = 4000
// 报告 A-10：任务卡的只读 GET 与写操作响应可能反序完成。loadGeneration 单调
// 递增，响应回来时只有最新一次请求（且 taskId 未切换）的结果允许写入，旧响应
// 不得覆盖较新状态；写操作完成时也递增代次，使在途的旧只读响应作废。
let loadGeneration = 0

const pendingApproval = computed(() => task.value?.pending_approval || null)
const pendingInputs = computed(() => task.value?.pending_inputs || [])
const approvalSummary = computed(() => String(pendingApproval.value?.payload?.summary || '').trim())
const approvalPayload = computed(() => pendingApproval.value?.payload?.canonical_payload || {})
const isTerminal = computed(() => {
  const status = task.value?.status
  return status === 'completed' || status === 'failed' || status === 'cancelled'
})

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
  if (type === 'select' || type === 'json_object' || type === 'string_list') return type
  return 'text'
}

/** 按 input_type 校验并序列化用户填写的待补字段。

报告 A-08：后端契约支持 text/select/json_object/string_list；旧实现把所有类型
都按字符串提交，真实 string_list/json_object/select 无法使用。这里按类型序列化：
select 必须是给定选项之一，json_object 必须能解析成 JSON 对象，string_list 按
换行/逗号拆成字符串数组。 */
function serializeInputValue(
  item: GlobalTaskRequiredInput,
  raw: string,
): { ok: true; value: unknown } | { ok: false; error: string } {
  const type = normalizedInputType(item)
  const trimmed = raw.trim()
  if (type === 'select') {
    if (!trimmed) return { ok: false, error: `请选择${item.label}。` }
    const options = item.options || []
    if (options.length && !options.includes(trimmed)) {
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
    task.value = response.task
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
    task.value = response.task
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
    task.value = response.task
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
    const raw = String(inputValues.value[item.key] || '')
    if (!raw.trim()) continue
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
    task.value = response.task
    inputValues.value = {}
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
    task.value = response.task
  } catch (error) {
    if (requestedTaskId !== props.taskId) return
    actionError.value = errorMessage(error)
  } finally {
    if (requestedTaskId === props.taskId) busyAction.value = ''
  }
}

watch(() => props.taskId, () => {
  // 报告 A-10：taskId 切换时递增代次，旧任务的在途慢响应不得写入新任务卡；
  // 同时清除旧任务的 busy 状态，新任务的按钮不再被旧在途操作禁用。
  loadGeneration += 1
  busyAction.value = ''
  task.value = null
  actionError.value = ''
  inputValues.value = {}
  void loadTask(false)
})

watch(isTerminal, (terminal) => {
  if (terminal) stopPolling()
  else startPolling()
})

onMounted(() => {
  if (!props.taskId) return
  // 只读挂载也先做一次纯 GET 读取以展示状态；轮询仅在可操作入口启用。
  void loadTask(false)
  if (props.enabled) startPolling()
})

onBeforeUnmount(() => {
  stopPolling()
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
      <span v-if="task.assistant_message"> · {{ task.assistant_message }}</span>
    </p>

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
        <!-- 报告 A-08：按 input_type 渲染控件；select 用选项下拉，json_object/
             string_list 用多行文本，text 用单行输入。 -->
        <select
          v-if="normalizedInputType(item) === 'select'"
          :id="`task-input-${item.key}`"
          v-model="inputValues[item.key]"
          class="input mt-1 text-xs"
          :data-testid="`global-task-input-${item.key}`"
        >
          <option disabled value="">请选择</option>
          <option v-for="option in item.options || []" :key="option" :value="option">
            {{ option }}
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
