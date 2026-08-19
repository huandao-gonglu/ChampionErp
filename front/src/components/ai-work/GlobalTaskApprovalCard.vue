<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import {
  approveGlobalTask,
  fetchGlobalTask,
  refreshGlobalTask,
  rejectGlobalTask,
} from '@/api/globalTasks'
import type { GlobalTaskResponse, GlobalTaskStatus } from '@/types/aiWork'

const props = withDefaults(defineProps<{
  response: unknown
  enabled?: boolean
}>(), {
  enabled: false,
})

const refreshedResponse = ref<GlobalTaskResponse | null>(null)
const busyAction = ref<'approve' | 'reject' | 'refresh' | ''>('')
const actionError = ref('')
const rejectionReason = ref('')

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {}
}

function normalizeResponse(value: unknown): GlobalTaskResponse | null {
  const response = asRecord(value)
  const task = asRecord(response.task)
  const taskId = String(response.task_id || task.task_id || '').trim()
  const status = String(task.status || '').trim()
  if (!taskId || !status) return null
  return {
    ok: true,
    task_id: taskId,
    task: task as unknown as GlobalTaskResponse['task'],
  }
}

const originalResponse = computed(() => normalizeResponse(props.response))
const currentResponse = computed(() => refreshedResponse.value || originalResponse.value)
const task = computed(() => currentResponse.value?.task || null)
const pendingApproval = computed(() => task.value?.pending_approval || null)
const approvalSummary = computed(() => String(pendingApproval.value?.payload?.summary || '').trim())
const approvalPayload = computed(() => pendingApproval.value?.payload?.canonical_payload || {})

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

async function refreshTask(showBusy = true): Promise<void> {
  const taskId = currentResponse.value?.task_id
  if (!taskId) return
  if (showBusy) busyAction.value = 'refresh'
  actionError.value = ''
  try {
    refreshedResponse.value = task.value?.status === 'in_progress'
      ? await refreshGlobalTask(taskId)
      : await fetchGlobalTask(taskId)
  } catch (error) {
    if (showBusy) actionError.value = errorMessage(error)
  } finally {
    if (showBusy) busyAction.value = ''
  }
}

async function approve(): Promise<void> {
  const taskId = currentResponse.value?.task_id
  const approval = pendingApproval.value
  if (!taskId || !approval || busyAction.value) return
  const summary = approvalSummary.value || `任务 ${taskId} 的当前步骤`
  if (!window.confirm(`确认执行以下高风险操作？\n\n${summary}`)) return
  busyAction.value = 'approve'
  actionError.value = ''
  try {
    refreshedResponse.value = await approveGlobalTask(taskId, approval.step_id)
  } catch (error) {
    actionError.value = errorMessage(error)
  } finally {
    busyAction.value = ''
  }
}

async function reject(): Promise<void> {
  const taskId = currentResponse.value?.task_id
  const approval = pendingApproval.value
  const reason = rejectionReason.value.trim()
  if (!taskId || !approval || !reason || busyAction.value) return
  busyAction.value = 'reject'
  actionError.value = ''
  try {
    refreshedResponse.value = await rejectGlobalTask(taskId, approval.step_id, reason)
    rejectionReason.value = ''
  } catch (error) {
    actionError.value = errorMessage(error)
  } finally {
    busyAction.value = ''
  }
}

watch(() => props.response, () => {
  refreshedResponse.value = null
  actionError.value = ''
})

onMounted(() => {
  if (props.enabled && originalResponse.value?.task_id) {
    void refreshTask(false)
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
      <span v-if="task.assistant_message"> · {{ task.assistant_message }}</span>
    </p>

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

    <button
      v-if="enabled && task.status !== 'pending_approval'"
      type="button"
      class="btn btn-outline mt-3 px-3 py-1.5 text-xs"
      data-testid="global-task-refresh"
      :disabled="Boolean(busyAction)"
      @click="refreshTask()"
    >
      {{ busyAction === 'refresh' ? '正在刷新…' : '刷新任务状态' }}
    </button>
  </section>
</template>
