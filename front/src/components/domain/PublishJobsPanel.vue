<script setup lang="ts">
import { computed, ref } from 'vue'
import { statusBadgeClass } from '@/utils/status'
import type { Marketplace, PublishJobListItem, UnknownRecord } from '@/types/workflow'

const props = defineProps<{
  jobs: PublishJobListItem[]
  selectedJobId: string
  selectedJobStatus: UnknownRecord | null
  loading: boolean
  nextCursor: string
  lastUpdated: string
  precheckOk: boolean
  activeMarketplace: Marketplace
  busy: boolean
}>()

const emit = defineEmits<{
  refresh: []
  select: [jobId: string]
  loadMore: []
  enqueue: []
  publishDirect: []
  confirmRealPublish: []
}>()

const statusFilter = ref('')
const platformFilter = ref('')
const searchQuery = ref('')

const statusLabels: Record<string, string> = {
  queued: '排队中',
  running: '发布中',
  success: '发布成功',
  failed: '发布失败',
  partial: '部分成功',
}

const stageLabels: Record<string, string> = {
  queued: '等待执行',
  resuming: '恢复执行',
  resolving_category: '解析类目',
  validating: '校验商品',
  validating_required_attributes: '校验必填属性',
  publishing: '提交平台',
  publishing_approved_payload: '提交已确认 Payload',
  waiting_platform_confirmation: '等待平台确认',
  retrying: '等待重试',
  finished: '已结束',
  failed: '已结束',
}

const platformOptions = computed(() => Array.from(new Set(
  props.jobs.flatMap((job) => job.platforms.map((item) => item.platform)),
)).sort())

const filteredJobs = computed(() => {
  const query = searchQuery.value.trim().toLowerCase()
  return props.jobs.filter((job) => {
    if (statusFilter.value && job.status !== statusFilter.value) return false
    if (platformFilter.value && !job.platforms.some((item) => item.platform === platformFilter.value)) return false
    if (!query) return true
    return [job.jobId, job.productId, job.productName, job.draftId, job.error]
      .some((value) => String(value || '').toLowerCase().includes(query))
  })
})

const selectedJob = computed(() => (
  props.jobs.find((job) => job.jobId === props.selectedJobId) || null
))

const detailForDisplay = computed(() => {
  if (!props.selectedJobStatus) return null
  const detail = { ...props.selectedJobStatus }
  delete detail.product
  return detail
})

function statusLabel(status: string) {
  return statusLabels[status] || status || '未知'
}

function stageLabel(stage: string) {
  return stageLabels[stage] || stage || '-'
}

function formatTime(value: string) {
  return String(value || '').replace('T', ' ').replace(/Z$/, '').slice(0, 19) || '-'
}

function selectJob(jobId: string) {
  emit('select', jobId)
}
</script>

<template>
  <section class="space-y-4">
    <div class="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h2 class="card-title">发布任务</h2>
        <p class="muted mt-1">每次发布入队生成一条独立任务，运行中的任务会自动刷新。</p>
      </div>
      <div class="flex flex-wrap gap-2">
        <button class="btn btn-outline" :disabled="loading" @click="emit('refresh')">刷新列表</button>
        <button class="btn btn-primary" :disabled="busy || !precheckOk" @click="emit('enqueue')">发布入队</button>
        <button class="btn btn-outline" :disabled="busy || activeMarketplace === 'mercadolibre' || !precheckOk" @click="emit('publishDirect')">非 ML 直接发布</button>
        <button class="btn btn-primary" :disabled="busy || activeMarketplace !== 'mercadolibre' || !precheckOk" @click="emit('confirmRealPublish')">确认 ML 真实发布</button>
      </div>
    </div>

    <div class="grid gap-4 xl:grid-cols-[minmax(0,1.45fr)_minmax(320px,0.75fr)]">
      <section class="min-w-0 rounded-lg border border-accent-200 bg-white p-4 shadow-card dark:border-dark-700 dark:bg-dark-900/80">
        <div class="grid gap-3 md:grid-cols-[minmax(0,1fr)_160px_160px]">
          <input v-model="searchQuery" class="input" placeholder="搜索 Job ID、商品或错误" />
          <select v-model="statusFilter" class="input">
            <option value="">全部状态</option>
            <option value="queued">排队中</option>
            <option value="running">发布中</option>
            <option value="success">发布成功</option>
            <option value="failed">发布失败</option>
            <option value="partial">部分成功</option>
          </select>
          <select v-model="platformFilter" class="input">
            <option value="">全部平台</option>
            <option v-for="platform in platformOptions" :key="platform" :value="platform">{{ platform }}</option>
          </select>
        </div>

        <div class="mt-4 overflow-x-auto rounded-lg border border-accent-200 dark:border-dark-700">
          <table class="min-w-[980px] w-full table-fixed text-left text-sm">
            <colgroup>
              <col class="w-[145px]" />
              <col class="w-[190px]" />
              <col class="w-[190px]" />
              <col class="w-[110px]" />
              <col class="w-[110px]" />
              <col class="w-[110px]" />
              <col class="w-[70px]" />
              <col />
            </colgroup>
            <thead class="border-b border-accent-200 bg-accent-50 text-xs text-accent-500 dark:border-dark-700 dark:bg-dark-950/70 dark:text-accent-400">
              <tr>
                <th class="p-3">创建时间</th>
                <th class="p-3">Job ID</th>
                <th class="p-3">商品</th>
                <th class="p-3">平台</th>
                <th class="p-3">状态</th>
                <th class="p-3">阶段</th>
                <th class="p-3">重试</th>
                <th class="p-3">结果摘要</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-accent-100 dark:divide-dark-800">
              <tr
                v-for="job in filteredJobs"
                :key="job.jobId"
                class="cursor-pointer align-top transition hover:bg-accent-50/70 dark:hover:bg-dark-800/60"
                :class="job.jobId === selectedJobId ? 'bg-accent-50 dark:bg-dark-800/80' : ''"
                @click="selectJob(job.jobId)"
              >
                <td class="p-3 text-accent-600 dark:text-accent-300">{{ formatTime(job.createdAt) }}</td>
                <td class="p-3 font-mono text-xs font-semibold text-accent-950 dark:text-white">
                  <button class="text-left hover:underline" :title="job.jobId" @click.stop="selectJob(job.jobId)">{{ job.jobId }}</button>
                </td>
                <td class="p-3">
                  <span class="block truncate font-medium text-accent-950 dark:text-white" :title="job.productName || job.productId">{{ job.productName || job.productId || '-' }}</span>
                  <span class="mt-1 block truncate text-xs text-accent-500 dark:text-accent-400">{{ job.productId || '-' }}</span>
                </td>
                <td class="p-3"><span v-for="item in job.platforms" :key="item.platform" class="badge-info mr-1">{{ item.platform }}</span></td>
                <td class="p-3"><span :class="statusBadgeClass(job.status)">{{ statusLabel(job.status) }}</span></td>
                <td class="p-3 text-accent-700 dark:text-accent-200">{{ stageLabel(job.stage) }}</td>
                <td class="p-3 text-center text-accent-700 dark:text-accent-200">{{ job.attempts }}</td>
                <td class="p-3 text-accent-700 dark:text-accent-200"><span class="block truncate" :title="job.error || '-'">{{ job.error || '-' }}</span></td>
              </tr>
              <tr v-if="!filteredJobs.length"><td colspan="8" class="p-8 text-center text-accent-500 dark:text-accent-300">暂无匹配的发布任务。</td></tr>
            </tbody>
          </table>
        </div>

        <div class="mt-3 flex items-center justify-between gap-3 text-xs text-accent-500 dark:text-accent-400">
          <span>已显示 {{ filteredJobs.length }} / {{ jobs.length }} 条<span v-if="lastUpdated"> · 更新于 {{ formatTime(lastUpdated) }}</span></span>
          <button v-if="nextCursor" class="btn btn-outline px-3 py-1.5 text-xs" :disabled="loading" @click="emit('loadMore')">加载更多</button>
        </div>
      </section>

      <aside class="min-w-0 rounded-lg border border-accent-200 bg-white p-4 shadow-card dark:border-dark-700 dark:bg-dark-900/80">
        <template v-if="selectedJob">
          <div class="flex items-start justify-between gap-3">
            <div class="min-w-0">
              <p class="text-xs text-accent-500 dark:text-accent-400">任务详情</p>
              <h3 class="mt-1 break-all font-mono text-sm font-semibold text-accent-950 dark:text-white">{{ selectedJob.jobId }}</h3>
            </div>
            <span :class="statusBadgeClass(selectedJob.status)">{{ statusLabel(selectedJob.status) }}</span>
          </div>

          <dl class="mt-4 grid grid-cols-[88px_minmax(0,1fr)] gap-x-3 gap-y-2 text-sm">
            <dt class="text-accent-500 dark:text-accent-400">商品</dt><dd class="break-words text-accent-950 dark:text-white">{{ selectedJob.productName || selectedJob.productId || '-' }}</dd>
            <dt class="text-accent-500 dark:text-accent-400">草稿</dt><dd class="break-all text-accent-700 dark:text-accent-200">{{ selectedJob.draftId || '-' }}</dd>
            <dt class="text-accent-500 dark:text-accent-400">创建</dt><dd class="text-accent-700 dark:text-accent-200">{{ formatTime(selectedJob.createdAt) }}</dd>
            <dt class="text-accent-500 dark:text-accent-400">更新</dt><dd class="text-accent-700 dark:text-accent-200">{{ formatTime(selectedJob.updatedAt) }}</dd>
            <dt class="text-accent-500 dark:text-accent-400">重试</dt><dd class="text-accent-700 dark:text-accent-200">{{ selectedJob.attempts }} 次</dd>
          </dl>

          <div v-if="selectedJob.error" class="mt-4 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800 dark:border-red-900/70 dark:bg-red-950/30 dark:text-red-200">
            <p class="font-semibold">失败原因</p>
            <p class="mt-1 break-words">{{ selectedJob.error }}</p>
          </div>

          <div class="mt-4 space-y-2">
            <article v-for="item in selectedJob.platforms" :key="item.platform" class="rounded-lg border border-accent-200 p-3 dark:border-dark-700">
              <div class="flex items-center justify-between gap-2">
                <span class="font-semibold text-accent-950 dark:text-white">{{ item.platform }}</span>
                <span :class="statusBadgeClass(item.status)">{{ statusLabel(item.status) }}</span>
              </div>
              <p class="mt-2 text-xs text-accent-500 dark:text-accent-400">{{ stageLabel(item.stage) }} · 尝试 {{ item.attempts }} 次</p>
              <p class="mt-1 break-all text-xs text-accent-500 dark:text-accent-400">{{ item.draftId || '-' }} · {{ item.site || '-' }}</p>
            </article>
          </div>

          <details v-if="detailForDisplay" class="mt-4">
            <summary class="cursor-pointer text-sm font-medium text-accent-700 dark:text-accent-200">查看技术详情</summary>
            <pre class="mt-2 max-h-80 overflow-auto rounded bg-slate-950 p-3 text-xs text-slate-100">{{ JSON.stringify(detailForDisplay, null, 2) }}</pre>
          </details>
        </template>
        <p v-else class="text-sm text-accent-500 dark:text-accent-300">选择一条任务查看平台状态和错误详情。</p>
      </aside>
    </div>
  </section>
</template>
