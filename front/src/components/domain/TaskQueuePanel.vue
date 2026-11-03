<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { PhX } from '@phosphor-icons/vue'
import PageHeader from '@/components/layout/PageHeader.vue'
import WorkspaceDialog from '@/components/shared/WorkspaceDialog.vue'
import { checkTask, fetchTask, fetchTasks, retryPublishTask } from '@/api/taskQueue'
import { onlineAction, type OnlineJob } from '@/api/onlineProducts'
import type { BackendQueueTask, BackendTaskQueueDetail } from '@/types/workflow.generated'
import OnlineOperationRecovery from './OnlineOperationRecovery.vue'

const props = defineProps<{ initialTaskId?: string; initialSource?: string }>()
const emit = defineEmits<{ 'close-task': [] }>()
const { t } = useI18n()
const filters = reactive({ q: '', source: props.initialSource || '', origin: '', status: '', platform: '', operation: '', account_id: '', since: '', until: '' })
const page = ref(1), total = ref(0), tasks = ref<BackendQueueTask[]>([]), accounts = ref<string[]>([])
const selectedId = ref(props.initialTaskId || ''), selected = ref<BackendTaskQueueDetail | null>(null)
const loading = ref(false), checking = ref(false), error = ref(''), notice = ref('')
const detailLoading = ref(false), detailError = ref('')
let timer: ReturnType<typeof setInterval> | undefined
let debounce: ReturnType<typeof setTimeout> | undefined
let disposed = false, generation = 0
let detailGeneration = 0
const statuses = ['queued', 'running', 'waiting', 'interrupted', 'needs_attention', 'completed', 'partial', 'failed', 'closed_unconfirmed']
const operations = ['publish', 'sync', 'price', 'stock', 'content', 'sale_state', 'archive']
const plan = computed(() => selected.value?.task.confirmation)
const onlineJob = computed(() => selected.value?.task.source === 'online' ? selected.value.detail as unknown as OnlineJob : null)
const canCheck = computed(() => selected.value && ['waiting', 'needs_attention'].includes(selected.value.task.status))
const changes = computed(() => onlineJob.value?.result.changes || onlineJob.value?.request.changes as Record<string, unknown> || {})
function record(value: unknown): Record<string, unknown> { return value && typeof value === 'object' ? value as Record<string, unknown> : {} }
const results = computed(() => {
  const result = record(selected.value?.detail.result)
  if (Array.isArray(result.sku_results)) return result.sku_results.map(record)
  const markets = record(result.publication).markets
  return Array.isArray(markets) ? markets.map(record) : []
})
const publishProgress = computed(() => {
  if (selected.value?.task.source !== 'publish' || !Array.isArray(record(selected.value.detail.result).sku_results)) return null
  const counts = { completed: 0, waiting: 0, pending: 0, unknown: 0, failed: 0, attention: 0 }
  for (const row of results.value) {
    const status = String(row.status)
    if (['published', 'success', 'real_publish_success', 'imported'].includes(status)) counts.completed++
    else if (['pending_confirmation', 'publish_pending_confirmation'].includes(status)) counts.waiting++
    else if (['queued', 'pending_submission'].includes(status)) counts.pending++
    else if (['outcome_unknown', 'dispatching'].includes(status)) counts.unknown++
    else if (status === 'needs_attention') counts.attention++
    else counts.failed++
  }
  return counts
})
function formatTime(value: string | number) {
  if (!value) return '—'
  return new Date(typeof value === 'number' ? value * 1000 : value).toLocaleString()
}
function display(value: unknown) { return value == null ? '—' : typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value) }
function statusLabel(value: unknown) {
  if (value === 'pending_submission') return t('taskQueue.pendingSubmission')
  if (value === 'outcome_unknown') return t('taskQueue.outcomeUnknown')
  const aliases: Record<string, string> = { success: 'completed', published: 'completed', confirmed: 'completed', submitted: 'completed', pending_confirmation: 'waiting', waiting_confirmation: 'waiting', outcome_unknown: 'needs_attention' }
  const status = aliases[String(value)] || String(value)
  return statuses.includes(status) ? t(`taskQueue.status.${status}`) : status
}
function badge(status: string) {
  return status === 'completed' ? 'badge-success' : status === 'failed' ? 'badge-danger' : ['needs_attention', 'partial'].includes(status) ? 'badge-muted' : 'badge-info'
}
async function loadDetail(id: string, quiet = false) {
  const current = ++detailGeneration
  const showLoading = !quiet || !selected.value
  if (showLoading) { detailLoading.value = true; detailError.value = '' }
  try {
    const detail = await fetchTask(id)
    if (!disposed && current === detailGeneration && selectedId.value === id) selected.value = detail
  } catch (e) {
    if (!disposed && current === detailGeneration && selectedId.value === id && showLoading) {
      detailError.value = e instanceof Error ? e.message : t('taskQueue.loadFailed')
    }
  } finally {
    if (current === detailGeneration) detailLoading.value = false
  }
}
function closeDetail() {
  if (checking.value) return
  detailGeneration++
  selectedId.value = ''; selected.value = null; detailLoading.value = false; detailError.value = ''; notice.value = ''
  emit('close-task')
}
async function load(quiet = false) {
  if (loading.value || checking.value) return
  const current = ++generation
  loading.value = true
  if (!quiet) error.value = ''
  try {
    const response = await fetchTasks({ ...filters, page: page.value })
    if (disposed || current !== generation) return
    tasks.value = response.items; total.value = response.total; accounts.value = response.accounts
    if (selectedId.value && !detailLoading.value) await loadDetail(selectedId.value, quiet)
  } catch (e) {
    if (!disposed && current === generation) error.value = e instanceof Error ? e.message : t('taskQueue.loadFailed')
  } finally { if (current === generation) loading.value = false }
}
async function select(task: BackendQueueTask) {
  if (checking.value) return
  selectedId.value = task.id; selected.value = null; notice.value = ''; error.value = ''
  await loadDetail(task.id)
}
async function check() {
  if (!selectedId.value || checking.value) return
  detailGeneration++; detailLoading.value = false
  checking.value = true; error.value = ''; notice.value = ''
  const id = selectedId.value
  try {
    const response = await checkTask(id)
    if (disposed || id !== selectedId.value) return
    selected.value = response
    notice.value = response.checked ? t('taskQueue.checked') : t(`taskQueue.checkResult.${response.resolution}`)
  } catch (e) { if (!disposed) error.value = e instanceof Error ? e.message : t('taskQueue.loadFailed') }
  finally { checking.value = false }
  if (!disposed) await load(true)
}
async function retry() {
  const task = selected.value?.task
  if (!task?.can_retry || checking.value) return
  detailGeneration++; detailLoading.value = false
  checking.value = true; error.value = ''; notice.value = ''
  try {
    let id = task.id
    if (task.source === 'publish') await retryPublishTask(task.job_id, task.platform)
    else {
      const job = await onlineAction('retry', { job_id: task.job_id, idempotency_key: crypto.randomUUID() })
      id = `online:${job.id}`
    }
    if (disposed || selectedId.value !== task.id) return
    selectedId.value = id
    selected.value = await fetchTask(selectedId.value)
    notice.value = t('taskQueue.retryQueued')
  } catch (e) { if (!disposed) error.value = e instanceof Error ? e.message : t('taskQueue.loadFailed') }
  finally { checking.value = false }
  if (!disposed) await load(true)
}
watch(() => props.initialTaskId, id => {
  detailGeneration++; detailLoading.value = false; detailError.value = ''; notice.value = ''; error.value = ''
  selectedId.value = id || ''; selected.value = null
  if (id) void loadDetail(id)
})
watch(() => props.initialSource, source => { filters.source = source || '' })
watch(filters, () => {
  generation++; loading.value = false; page.value = 1
  clearTimeout(debounce)
  debounce = setTimeout(() => { void load() }, 250)
})
onMounted(() => {
  void load()
  if (selectedId.value) void loadDetail(selectedId.value)
  timer = setInterval(() => { if (!document.hidden) void load(true) }, 5000)
})
onBeforeUnmount(() => { disposed = true; detailGeneration++; clearInterval(timer); clearTimeout(debounce) })
</script>

<template>
  <section class="space-y-6">
    <PageHeader :title="t('pages.publish.title')" :description="t('pages.publish.description')">
      <template #actions><button class="btn btn-outline" :disabled="loading || checking" @click="load()">{{ t('taskQueue.refresh') }}</button></template>
    </PageHeader>
    <p v-if="error && !selectedId" role="alert" class="text-sm text-red-700 dark:text-red-300">{{ error }}</p>
    <div class="grid gap-3 sm:grid-cols-3 xl:grid-cols-5">
      <input v-model="filters.q" class="input" :placeholder="t('taskQueue.search')" :aria-label="t('taskQueue.search')" />
      <select v-model="filters.source" class="input" :aria-label="t('taskQueue.source')"><option value="">{{ t('taskQueue.allSources') }}</option><option value="publish">{{ t('taskQueue.publish') }}</option><option value="online">{{ t('taskQueue.online') }}</option></select>
      <select v-model="filters.origin" class="input" :aria-label="t('taskQueue.origin')"><option value="">{{ t('taskQueue.allOrigins') }}</option><option value="page">{{ t('taskQueue.page') }}</option><option value="ai">AI</option><option value="unknown">{{ t('taskQueue.unknown') }}</option></select>
      <select v-model="filters.status" class="input" :aria-label="t('taskQueue.state')"><option value="">{{ t('taskQueue.allStatuses') }}</option><option v-for="status in statuses" :key="status" :value="status">{{ t(`taskQueue.status.${status}`) }}</option></select>
      <select v-model="filters.platform" class="input" :aria-label="t('taskQueue.platform')"><option value="">{{ t('taskQueue.allPlatforms') }}</option><option value="ozon">Ozon</option><option value="yandex">Yandex</option><option value="mercadolibre">Mercado Libre</option></select>
      <select v-model="filters.operation" class="input" :aria-label="t('taskQueue.operation')"><option value="">{{ t('taskQueue.allOperations') }}</option><option v-for="operation in operations" :key="operation" :value="operation">{{ t(`taskQueue.operations.${operation}`) }}</option></select>
      <select v-model="filters.account_id" class="input" :aria-label="t('taskQueue.account')"><option value="">{{ t('taskQueue.allAccounts') }}</option><option v-for="account in accounts" :key="account" :value="account">{{ account }}</option></select>
      <label class="text-xs text-accent-500">{{ t('taskQueue.since') }}<input v-model="filters.since" type="date" class="input mt-1" /></label>
      <label class="text-xs text-accent-500">{{ t('taskQueue.until') }}<input v-model="filters.until" type="date" class="input mt-1" /></label>
    </div>
    <div class="min-w-0 overflow-auto rounded-lg border border-accent-200 dark:border-dark-700">
      <table class="w-full min-w-[760px] text-left text-sm">
        <thead class="bg-accent-50 text-accent-500 dark:bg-dark-900"><tr><th class="p-3">{{ t('taskQueue.task') }}</th><th class="p-3">{{ t('taskQueue.source') }}</th><th class="p-3">{{ t('taskQueue.platform') }}</th><th class="p-3">{{ t('taskQueue.state') }}</th><th class="p-3">{{ t('taskQueue.createdAt') }}</th></tr></thead>
        <tbody class="divide-y divide-accent-100 dark:divide-dark-800">
          <tr v-for="task in tasks" :key="task.id" :class="task.id === selectedId ? 'bg-accent-50 dark:bg-dark-800' : ''">
            <td class="p-3"><button class="text-left font-medium hover:underline" :disabled="checking" @click="select(task)">{{ task.title }}</button><p class="mt-1 font-mono text-xs text-accent-500">{{ task.job_id }}</p><p class="mt-1 text-xs">{{ t(`taskQueue.operations.${task.operation}`) }}</p></td>
            <td class="p-3">{{ t(`taskQueue.${task.source === 'publish' ? 'publish' : 'online'}`) }}<p class="mt-1 text-xs text-accent-500">{{ task.origin === 'ai' ? 'AI' : t(`taskQueue.${task.origin === 'page' ? 'page' : 'unknown'}`) }}</p></td>
            <td class="p-3">{{ task.platform }}</td>
            <td class="p-3"><span :class="badge(task.status)">{{ statusLabel(task.status) }}</span></td>
            <td class="p-3 text-xs">{{ formatTime(task.created_at) }}</td>
          </tr>
          <tr v-if="!tasks.length"><td colspan="5" class="p-10 text-center text-accent-500">{{ t('taskQueue.empty') }}</td></tr>
        </tbody>
      </table>
      <div class="flex items-center justify-between border-t border-accent-200 p-3 text-sm dark:border-dark-700"><span>{{ t('taskQueue.total', { total }) }}</span><div class="flex items-center gap-3"><button class="btn btn-outline" :disabled="page <= 1 || loading" @click="page--; load()">{{ t('taskQueue.previous') }}</button><span>{{ page }}</span><button class="btn btn-outline" :disabled="page * 30 >= total || loading" @click="page++; load()">{{ t('taskQueue.next') }}</button></div></div>
    </div>
    <WorkspaceDialog v-if="selectedId" :open="true" :title="t('taskQueue.detail')" variant="drawer" width="640px" :modal="false" :close-disabled="checking" @close="closeDetail">
      <template #header="{ titleId }">
        <div class="min-w-0 flex-1">
          <p :id="titleId" class="text-sm text-accent-500 dark:text-accent-300">{{ t('taskQueue.detail') }}</p>
          <div class="mt-1 flex flex-wrap items-center gap-3"><h2 class="min-w-0 break-words text-lg font-bold">{{ selected?.task.title || selectedId }}</h2><span v-if="selected" :class="badge(selected.task.status)">{{ statusLabel(selected.task.status) }}</span></div>
        </div>
      </template>
      <template #close><PhX :size="22" aria-hidden="true" /></template>
      <p v-if="detailLoading" role="status" class="muted py-8 text-center">{{ t('taskQueue.loading') }}</p>
      <p v-if="detailError || error" role="alert" class="mb-4 text-sm text-red-700 dark:text-red-300">{{ detailError || error }} <button v-if="detailError" class="underline" @click="loadDetail(selectedId)">{{ t('taskQueue.reload') }}</button></p>
      <p v-if="notice" role="status" class="mb-4 text-sm text-accent-600 dark:text-accent-300">{{ notice }}</p>
      <template v-if="selected">
        <p class="break-all font-mono text-xs text-accent-500">{{ selected.task.job_id }}</p>
        <dl class="mt-4 grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-2 text-sm"><dt>{{ t('taskQueue.target') }}</dt><dd class="break-all">{{ selected.task.target_id }}</dd><dt>{{ t('taskQueue.account') }}</dt><dd class="break-all">{{ selected.task.account_id || '—' }}</dd><dt>{{ t('taskQueue.updatedAt') }}</dt><dd>{{ formatTime(selected.task.updated_at) }}</dd><template v-if="selected.task.source === 'publish'"><dt>{{ t('taskQueue.executionAttempts') }}</dt><dd>{{ selected.detail.attempts || 0 }}</dd><dt>{{ t('taskQueue.retryAttempts') }}</dt><dd>{{ Math.max(0, Number(selected.detail.attempts || 0) - 1) }}</dd></template></dl>
        <p v-if="selected.task.protection" class="mt-4 rounded bg-amber-50 p-3 text-sm text-amber-800 dark:bg-amber-950/30 dark:text-amber-200">{{ selected.task.protection }}</p>
        <p v-if="selected.task.error" class="mt-3 whitespace-pre-wrap break-words text-sm text-red-700 dark:text-red-300">{{ selected.task.error }}</p>
        <dl v-if="plan" class="mt-4 grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-2 text-sm"><dt>{{ t('taskQueue.lastCheck') }}</dt><dd>{{ formatTime(plan.last_checked_at) }}</dd><dt>{{ t('taskQueue.nextCheck') }}</dt><dd>{{ formatTime(plan.next_check_at) }}</dd><dt>{{ t('taskQueue.remaining') }}</dt><dd>{{ plan.checks_remaining }}</dd><dt>{{ t('taskQueue.requestsRemaining') }}</dt><dd>{{ plan.requests_remaining }}</dd><dt>{{ t('taskQueue.deadline') }}</dt><dd>{{ formatTime(plan.deadline_at) }}</dd></dl>
        <p v-if="plan?.error" class="mt-3 text-sm text-amber-700 dark:text-amber-300">{{ plan.error }}</p>
        <div class="mt-4 flex flex-wrap gap-2"><button v-if="canCheck" class="btn btn-outline" :disabled="checking || plan?.state === 'checking'" @click="check">{{ checking ? t('taskQueue.checking') : t('taskQueue.check') }}</button><OnlineOperationRecovery v-if="onlineJob && onlineJob.operation !== 'sync'" :job="onlineJob" :disabled="checking" @updated="load()" /><button v-if="selected.task.can_retry" class="btn btn-outline" :disabled="checking" @click="retry">{{ t(selected.task.source === 'publish' ? 'taskQueue.retryPublish' : 'taskQueue.retry') }}</button></div>
        <p v-if="onlineJob?.operation === 'sync'" role="status" class="mt-4 text-sm">{{ t('taskQueue.syncProgress', { completed: onlineJob.result.completed || 0, failed: onlineJob.result.failed || 0, discovered: onlineJob.result.discovered ?? '—' }) }}</p>
        <ul v-if="onlineJob?.operation === 'sync'" class="mt-3 max-h-80 space-y-2 overflow-auto text-sm"><li v-for="item in onlineJob.result.items?.filter(item => item.status === 'failed')" :key="item.remote_id">{{ item.remote_id }}：{{ item.error }}</li></ul>
        <div v-if="onlineJob && Object.keys(changes).length" class="mt-4 overflow-auto"><table class="w-full table-fixed text-left text-xs"><thead><tr><th class="w-1/4">{{ t('taskQueue.field') }}</th><th>{{ t('taskQueue.requested') }}</th><th>{{ t('taskQueue.observed') }}</th></tr></thead><tbody><tr v-for="(value, field) in changes" :key="field"><td class="break-all p-2 align-top">{{ field }}</td><td class="p-2"><pre class="max-h-48 overflow-auto whitespace-pre-wrap break-all">{{ display(value) }}</pre></td><td class="p-2"><pre class="max-h-48 overflow-auto whitespace-pre-wrap break-all">{{ display(onlineJob.result.evidence?.observed?.[field]) }}</pre></td></tr></tbody></table></div>
        <ul v-if="onlineJob?.result.platform_errors?.length" class="mt-4 space-y-2 text-sm text-red-700 dark:text-red-300"><li v-for="(item, index) in onlineJob.result.platform_errors" :key="index" class="whitespace-pre-wrap break-words">{{ display(item) }}</li></ul>
        <p v-if="publishProgress" class="mt-4 text-sm" role="status">{{ t('taskQueue.publishProgress', publishProgress) }}</p>
        <ul v-if="results.length" class="mt-4 space-y-2"><li v-for="(item, index) in results" :key="index" class="rounded border border-accent-200 p-3 text-sm dark:border-dark-700"><p>{{ item.sku || item.site_id || item.sku_id }} · {{ statusLabel(item.status) }}</p><p v-if="item.error" class="mt-1 break-words text-red-600 dark:text-red-300">{{ display(item.error) }}</p></li></ul>
        <details class="mt-5 text-xs"><summary class="cursor-pointer text-accent-500">{{ t('taskQueue.technical') }}</summary><pre class="mt-3 max-h-96 overflow-auto whitespace-pre-wrap break-all">{{ display(selected.detail) }}</pre></details>
      </template>
    </WorkspaceDialog>
  </section>
</template>
