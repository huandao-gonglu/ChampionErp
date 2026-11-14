<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { PhX } from '@phosphor-icons/vue'
import PageHeader from '@/components/layout/PageHeader.vue'
import WorkspaceDialog from '@/components/shared/WorkspaceDialog.vue'
import { checkTask, fetchTask, fetchTasks, retryPublishTask } from '@/api/taskQueue'
import { onlineAction, type OnlineJob } from '@/api/onlineProducts'
import type { BackendQueueTask, BackendTaskQueueDetail } from '@/types/workflow.generated'
import OnlineOperationRecovery from './OnlineOperationRecovery.vue'

const props = defineProps<{ initialTaskId?: string; initialSource?: string }>()
const emit = defineEmits<{ 'close-task': [] }>()
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
const canCheck = computed(() => selected.value?.task.can_check)
const changes = computed(() => onlineJob.value?.result.changes || onlineJob.value?.request.changes as Record<string, unknown> || {})
function record(value: unknown): Record<string, unknown> { return value && typeof value === 'object' ? value as Record<string, unknown> : {} }
const results = computed(() => {
  const result = record(selected.value?.detail.result)
  if (Array.isArray(result.sku_results)) return result.sku_results.map(record)
  const markets = record(result.publication).markets
  return Array.isArray(markets) ? markets.map(record) : []
})
function publishWarnings(item: Record<string, unknown>): string[] {
  const warnings = record(item.result).warnings
  return Array.isArray(warnings) ? warnings.map(warning => {
    const row = record(warning)
    return [row.message, row.comment].filter(Boolean).map(String).join('：') || String(warning)
  }) : []
}
const stepLabels: Record<string, string> = { offer_mapping: '商品资料', campaign_offer: '上架条件', price: '价格', stock: '库存' }
function checkpoint(item: Record<string, unknown>) {
  const result = record(item.result)
  return record(record(result.result).checkpoint || result.checkpoint)
}
function submissionProgress(item: Record<string, unknown>) {
  const steps = checkpoint(item).completed_steps
  if (!Array.isArray(steps) || !steps.length) return ''
  return '已提交' + steps.map(step => stepLabels[String(step)] || String(step)).join('、')
}
function campaignStatus(item: Record<string, unknown>) {
  const evidence = record(checkpoint(item).evidence)
  return String(record(evidence.confirmation).campaign_status || record(evidence.campaign_observation).status || '')
}
const observedSales = computed(() => {
  const observed = results.value.map(campaignStatus).filter(Boolean)
  return observed.length ? { total: observed.length, active: observed.filter(status => status === 'PUBLISHED').length } : null
})
const publishProgress = computed(() => {
  if (selected.value?.task.source !== 'publish' || !Array.isArray(record(selected.value.detail.result).sku_results)) return null
  const counts = { completed: 0, waiting: 0, pending: 0, unknown: 0, failed: 0, attention: 0 }
  for (const row of results.value) {
    const status = String(row.status)
    if (['published', 'success', 'real_publish_success', 'imported'].includes(status)) counts.completed++
    else if (['pending_confirmation', 'publish_pending_confirmation'].includes(status)) counts.waiting++
    else if (['queued', 'pending_submission', 'submission_paused'].includes(status)) counts.pending++
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
  if (value === 'submission_paused') return '等待手动续提'
  if (value === 'pending_submission') return '待提交'
  if (value === 'outcome_unknown') return '结果未知'
  const aliases: Record<string, string> = { success: 'completed', published: 'completed', confirmed: 'completed', submitted: 'completed', pending_confirmation: 'waiting', waiting_confirmation: 'waiting', outcome_unknown: 'needs_attention' }
  const status = aliases[String(value)] || String(value)
  return statuses.includes(status) ? statusLabels[status] : status
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
      detailError.value = e instanceof Error ? e.message : '读取任务失败'
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
    if (!disposed && current === generation) error.value = e instanceof Error ? e.message : '读取任务失败'
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
    notice.value = checkMessages[response.resolution] || (response.checked ? '已查询，任务结果已更新' : '')
  } catch (e) { if (!disposed) error.value = e instanceof Error ? e.message : '读取任务失败' }
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
    notice.value = '重试任务已入队'
  } catch (e) { if (!disposed) error.value = e instanceof Error ? e.message : '读取任务失败' }
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

const sourceLabels: Record<string, string> = {
  publish: '商品发布',
  online: '在线商品',
  page: '页面',
  unknown: '历史记录',
}

const checkMessages: Record<string, string> = {
  query_failed: '部分或全部结果尚未查明，请查看查询回执',
  cooldown: '刚刚查询过，请稍后再查',
  checking: '已有结果查询正在执行',
  budget_exhausted: '自动确认已停止，可手动查询平台结果或在平台后台核对',
  unqueryable: '没有可查询的远端回执，请在平台后台核对',
  terminal_or_submitting: '任务已结束或仍在提交',
  not_due: '尚未到查询时间',
}

const operationLabels: Record<string, string> = {
  sync: '同步商品',
  publish: '发布商品',
  price: '修改价格',
  stock: '修改库存',
  content: '修改内容',
  sale_state: '停售 / 恢复',
  archive: '归档 / 恢复归档',
}

const statusLabels: Record<string, string> = {
  interrupted: '已中断',
  queued: '排队中',
  running: '执行中',
  waiting: '等待结果',
  needs_attention: '需要处理',
  completed: '已完成',
  partial: '部分完成',
  failed: '失败',
  closed_unconfirmed: '已结束，未确认',
}
</script>

<template>
  <section class="space-y-6">
    <PageHeader title="任务队列" description="集中查看发布、商品同步和修改的进度与结果。">
      <template #actions><button class="btn btn-outline" :disabled="loading || checking" @click="load()">刷新列表</button></template>
    </PageHeader>
    <p v-if="error && !selectedId" role="alert" class="text-sm text-red-700 dark:text-red-300">{{ error }}</p>
    <div class="grid gap-3 sm:grid-cols-3 xl:grid-cols-5">
      <input v-model="filters.q" class="input" placeholder="搜索任务号或商品" aria-label="搜索任务号或商品" />
      <select v-model="filters.source" class="input" aria-label="业务来源"><option value="">全部来源</option><option value="publish">商品发布</option><option value="online">在线商品</option></select>
      <select v-model="filters.origin" class="input" aria-label="发起方式"><option value="">全部发起方式</option><option value="page">页面</option><option value="ai">AI</option><option value="unknown">历史记录</option></select>
      <select v-model="filters.status" class="input" aria-label="状态"><option value="">全部状态</option><option v-for="status in statuses" :key="status" :value="status">{{ statusLabels[status] }}</option></select>
      <select v-model="filters.platform" class="input" aria-label="平台"><option value="">全部平台</option><option value="ozon">Ozon</option><option value="yandex">Yandex</option><option value="mercadolibre">Mercado Libre</option></select>
      <select v-model="filters.operation" class="input" aria-label="操作类型"><option value="">全部操作</option><option v-for="operation in operations" :key="operation" :value="operation">{{ operationLabels[operation] }}</option></select>
      <select v-model="filters.account_id" class="input" aria-label="店铺"><option value="">全部店铺</option><option v-for="account in accounts" :key="account" :value="account">{{ account }}</option></select>
      <label class="text-xs text-accent-500">开始日期<input v-model="filters.since" type="date" class="input mt-1" /></label>
      <label class="text-xs text-accent-500">结束日期<input v-model="filters.until" type="date" class="input mt-1" /></label>
    </div>
    <div class="min-w-0 overflow-auto rounded-lg border border-accent-200 dark:border-dark-700">
      <table class="w-full min-w-[760px] text-left text-sm">
        <thead class="bg-accent-50 text-accent-500 dark:bg-dark-900"><tr><th class="p-3">任务 / 商品</th><th class="p-3">业务来源</th><th class="p-3">平台</th><th class="p-3">状态</th><th class="p-3">创建时间</th></tr></thead>
        <tbody class="divide-y divide-accent-100 dark:divide-dark-800">
          <tr v-for="task in tasks" :key="task.id" :class="task.id === selectedId ? 'bg-accent-50 dark:bg-dark-800' : ''">
            <td class="p-3"><button class="text-left font-medium hover:underline" :disabled="checking" @click="select(task)">{{ task.title }}</button><p class="mt-1 font-mono text-xs text-accent-500">{{ task.job_id }}</p><p class="mt-1 text-xs">{{ operationLabels[task.operation] }}</p></td>
            <td class="p-3">{{ sourceLabels[task.source === 'publish' ? 'publish' : 'online'] }}<p class="mt-1 text-xs text-accent-500">{{ task.origin === 'ai' ? 'AI' : sourceLabels[task.origin === 'page' ? 'page' : 'unknown'] }}</p></td>
            <td class="p-3">{{ task.platform }}</td>
            <td class="p-3"><span :class="badge(task.status)">{{ statusLabel(task.status) }}</span></td>
            <td class="p-3 text-xs">{{ formatTime(task.created_at) }}</td>
          </tr>
          <tr v-if="!tasks.length"><td colspan="5" class="p-10 text-center text-accent-500">暂无匹配的任务</td></tr>
        </tbody>
      </table>
      <div class="flex items-center justify-between border-t border-accent-200 p-3 text-sm dark:border-dark-700"><span>{{ `共 ${total} 条` }}</span><div class="flex items-center gap-3"><button class="btn btn-outline" :disabled="page <= 1 || loading" @click="page--; load()">上一页</button><span>{{ page }}</span><button class="btn btn-outline" :disabled="page * 30 >= total || loading" @click="page++; load()">下一页</button></div></div>
    </div>
    <WorkspaceDialog v-if="selectedId" :open="true" title="任务详情" variant="drawer" width="640px" :modal="false" :close-disabled="checking" @close="closeDetail">
      <template #header="{ titleId }">
        <div class="min-w-0 flex-1">
          <p :id="titleId" class="text-sm text-accent-500 dark:text-accent-300">任务详情</p>
          <div class="mt-1 flex flex-wrap items-center gap-3"><h2 class="min-w-0 break-words text-lg font-bold">{{ selected?.task.title || selectedId }}</h2><span v-if="selected" :class="badge(selected.task.status)">{{ statusLabel(selected.task.status) }}</span></div>
        </div>
      </template>
      <template #close><PhX :size="22" aria-hidden="true" /></template>
      <p v-if="detailLoading" role="status" class="muted py-8 text-center">正在读取任务详情…</p>
      <p v-if="detailError || error" role="alert" class="mb-4 text-sm text-red-700 dark:text-red-300">{{ detailError || error }} <button v-if="detailError" class="underline" @click="loadDetail(selectedId)">重新读取</button></p>
      <p v-if="notice" role="status" class="mb-4 text-sm text-accent-600 dark:text-accent-300">{{ notice }}</p>
      <template v-if="selected">
        <p class="break-all font-mono text-xs text-accent-500">{{ selected.task.job_id }}</p>
        <dl class="mt-4 grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-2 text-sm"><dt>目标</dt><dd class="break-all">{{ selected.task.target_id }}</dd><dt>店铺</dt><dd class="break-all">{{ selected.task.account_id || '—' }}</dd><dt>更新时间</dt><dd>{{ formatTime(selected.task.updated_at) }}</dd><template v-if="selected.task.source === 'publish'"><dt>发布执行次数</dt><dd>{{ selected.detail.attempts || 0 }}</dd><dt>手动重试次数</dt><dd>{{ Math.max(0, Number(selected.detail.attempts || 0) - 1) }}</dd></template></dl>
        <p v-if="selected.task.protection" class="mt-4 rounded bg-amber-50 p-3 text-sm text-amber-800 dark:bg-amber-950/30 dark:text-amber-200">{{ selected.task.protection }}</p>
        <p v-if="selected.task.error" class="mt-3 whitespace-pre-wrap break-words text-sm text-red-700 dark:text-red-300">{{ selected.task.error }}</p>
        <dl v-if="plan" class="mt-4 grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-2 text-sm"><dt>最近查询</dt><dd>{{ formatTime(plan.last_checked_at) }}</dd><dt>下次查询</dt><dd>{{ formatTime(plan.next_check_at) }}</dd><dt>自动查询剩余轮数</dt><dd>{{ plan.checks_remaining }}</dd><dt>自动查询剩余请求数</dt><dd>{{ plan.requests_remaining }}</dd><dt>自动确认截止时间</dt><dd>{{ formatTime(plan.deadline_at) }}</dd></dl>
        <p v-if="plan?.error" class="mt-3 text-sm text-amber-700 dark:text-amber-300">{{ plan.error }}</p>
        <div class="mt-4 flex flex-wrap gap-2"><button v-if="canCheck" class="btn btn-outline" :disabled="checking || plan?.state === 'checking'" @click="check">{{ checking ? '正在查询…' : '查询平台结果' }}</button><OnlineOperationRecovery v-if="onlineJob && onlineJob.operation !== 'sync'" :job="onlineJob" :disabled="checking" @updated="load()" /><button v-if="selected.task.can_retry" class="btn btn-outline" :disabled="checking || plan?.state === 'checking'" @click="retry">{{ (selected.task.source === 'publish' ? '继续提交未完成步骤' : '仅重试失败项') }}</button></div>
        <p v-if="onlineJob?.operation === 'sync'" role="status" class="mt-4 text-sm">{{ `已发现 ${onlineJob.result.discovered ?? '—'} 件 · 成功 ${onlineJob.result.completed || 0} 件 · 失败 ${onlineJob.result.failed || 0} 件` }}</p>
        <ul v-if="onlineJob?.operation === 'sync'" class="mt-3 max-h-80 space-y-2 overflow-auto text-sm"><li v-for="item in onlineJob.result.items?.filter(item => item.status === 'failed')" :key="item.remote_id">{{ item.remote_id }}：{{ item.error }}</li></ul>
        <div v-if="onlineJob && Object.keys(changes).length" class="mt-4 overflow-auto"><table class="w-full table-fixed text-left text-xs"><thead><tr><th class="w-1/4">修改项</th><th>目标值</th><th>平台当前值</th></tr></thead><tbody><tr v-for="(value, field) in changes" :key="field"><td class="break-all p-2 align-top">{{ field }}</td><td class="p-2"><pre class="max-h-48 overflow-auto whitespace-pre-wrap break-all">{{ display(value) }}</pre></td><td class="p-2"><pre class="max-h-48 overflow-auto whitespace-pre-wrap break-all">{{ display(onlineJob.result.evidence?.observed?.[field]) }}</pre></td></tr></tbody></table></div>
        <ul v-if="onlineJob?.result.platform_errors?.length" class="mt-4 space-y-2 text-sm text-red-700 dark:text-red-300"><li v-for="(item, index) in onlineJob.result.platform_errors" :key="index" class="whitespace-pre-wrap break-words">{{ display(item) }}</li></ul>
        <p v-if="publishProgress" class="mt-4 text-sm" role="status">{{ `已发布 ${publishProgress.completed} · 待确认 ${publishProgress.waiting} · 待提交 ${publishProgress.pending} · 结果未知 ${publishProgress.unknown} · 失败 ${publishProgress.failed} · 需处理 ${publishProgress.attention}` }}</p>
        <p v-if="observedSales" class="mt-1 text-sm">平台在售 {{ observedSales.active }} · 已查到店铺状态 {{ observedSales.total }}</p>
        <ul v-if="results.length" class="mt-4 space-y-2"><li v-for="(item, index) in results" :key="index" class="rounded border border-accent-200 p-3 text-sm dark:border-dark-700"><p>{{ item.sku || item.site_id || item.sku_id }} · {{ statusLabel(item.status) }}</p><p v-if="submissionProgress(item)" class="mt-1 text-xs">{{ submissionProgress(item) }}</p><p v-if="campaignStatus(item)" class="mt-1 text-xs">店铺状态 · {{ campaignStatus(item) === 'PUBLISHED' ? '在售' : campaignStatus(item) }}</p><p v-if="item.error" class="mt-1 break-words text-red-600 dark:text-red-300">{{ display(item.error) }}</p><p v-for="(warning, warningIndex) in publishWarnings(item)" :key="warningIndex" class="mt-1 whitespace-pre-wrap break-words text-amber-700 dark:text-amber-300">{{ `内容警告：${warning}` }}</p></li></ul>
        <details class="mt-5 text-xs"><summary class="cursor-pointer text-accent-500">查看平台回执</summary><pre class="mt-3 max-h-96 overflow-auto whitespace-pre-wrap break-all">{{ display(selected.detail) }}</pre></details>
      </template>
    </WorkspaceDialog>
  </section>
</template>
