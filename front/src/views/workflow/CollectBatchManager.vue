<script setup lang="ts">
import { computed, ref } from 'vue'
import type { CollectBatchRow, CollectBatchStatus } from '@/types/workflow'
import { appendCollectUrls, collectBatchStatusLabels, createCollectBatchRow, normalizeCollectUrl } from '@/utils/collectQueue'

const props = defineProps<{ rows: CollectBatchRow[]; loading: boolean }>()
const emit = defineEmits<{ update: [rows: CollectBatchRow[]]; collect: [rowIds?: string[]] }>()
const input = ref('')
const message = ref('')
const error = ref('')
const editingId = ref('')
const editingUrl = ref('')
const filter = ref<CollectBatchStatus | ''>('')
const counts = computed(() => props.rows.reduce((total, row) => {
  total[row.status]++
  return total
}, { pending: 0, running: 0, waiting_verification: 0, success: 0, failed: 0 }))
const visibleRows = computed(() => props.rows.filter((row) => !filter.value || row.status === filter.value))
const badgeClasses: Record<CollectBatchStatus, string> = {
  waiting_verification: 'bg-amber-50 text-amber-800 dark:bg-amber-500/15 dark:text-amber-200',
  pending: 'bg-slate-100 text-slate-600 dark:bg-dark-700 dark:text-accent-200',
  running: 'bg-blue-50 text-blue-700 dark:bg-blue-500/15 dark:text-blue-200',
  success: 'bg-emerald-50 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-200',
  failed: 'bg-rose-50 text-rose-700 dark:bg-rose-500/15 dark:text-rose-200',
}

function addUrls() {
  if (props.loading) return
  error.value = ''
  try {
    const result = appendCollectUrls(props.rows, input.value)
    emit('update', result.rows)
    input.value = ''
    filter.value = ''
    message.value = `已添加 ${result.added} 条${result.duplicates ? `，跳过 ${result.duplicates} 条重复链接` : ''}。`
  } catch (exc) {
    message.value = ''
    error.value = (exc as Error).message
  }
}

function editRow(row: CollectBatchRow) {
  editingId.value = row.id
  editingUrl.value = row.url
  error.value = ''
}

function saveEdit() {
  if (props.loading) return
  error.value = ''
  try {
    const url = normalizeCollectUrl(editingUrl.value)
    if (props.rows.some((row) => row.id !== editingId.value && row.url === url)) throw new Error('此 URL 已在列表中，请勿重复添加。')
    emit('update', props.rows.map((row) => row.id === editingId.value && row.url !== url ? createCollectBatchRow(url, row.id) : row))
    editingId.value = ''
    message.value = '链接已保存；修改后的链接将重新等待采集。'
  } catch (exc) { error.value = (exc as Error).message }
}

function removeRow(id: string) {
  if (props.loading) return
  emit('update', props.rows.filter((row) => row.id !== id))
  if (editingId.value === id) editingId.value = ''
  message.value = '已从采集列表移除，已入库的商品保留。'
}
</script>

<template>
  <section data-testid="collect-batch-manager" class="space-y-5">
    <div>
      <h3 class="card-title">批量 URL 管理</h3>
      <p class="muted mt-1">添加链接后逐条采集，自动识别来源平台。列表保存在本机浏览器；采集时请保持应用打开。来源缺少包装资料仍可入库，核价和发布前需在 SKU 页补齐。</p>
    </div>
    <div class="rounded-2xl border border-slate-200 bg-slate-50 p-4 dark:border-dark-700 dark:bg-dark-900/60">
      <label for="batch-url-input" class="text-sm font-semibold">添加商品链接</label>
      <textarea id="batch-url-input" v-model="input" :disabled="props.loading" class="input mt-2 min-h-24 font-mono text-sm" placeholder="每行一个 http:// 或 https:// 商品链接，自动去重" />
      <div class="mt-3 flex flex-wrap items-center justify-between gap-3">
        <span class="muted text-xs">支持一次粘贴多个 URL，添加后可逐条编辑或删除。</span>
        <button class="btn btn-secondary" :disabled="props.loading || !input.trim()" @click="addUrls">添加到列表</button>
      </div>
    </div>
    <p v-if="error" role="alert" class="rounded-xl bg-rose-50 p-3 text-sm text-rose-700 dark:bg-rose-500/10 dark:text-rose-200">{{ error }}</p>
    <p v-else-if="message" role="status" class="text-sm text-slate-600 dark:text-accent-300">{{ message }}</p>
    <div class="flex flex-wrap items-center gap-2" aria-label="按采集状态筛选">
      <button class="rounded-lg px-3 py-2 text-sm ring-1 ring-slate-200 dark:ring-dark-700" :class="!filter ? 'bg-primary-50 font-semibold text-primary-700 dark:bg-primary-500/15 dark:text-primary-200' : ''" :aria-pressed="!filter" @click="filter = ''">全部 {{ props.rows.length }}</button>
      <button v-for="(label, status) in collectBatchStatusLabels" :key="status" class="rounded-lg px-3 py-2 text-sm" :class="[badgeClasses[status], filter === status ? 'ring-2 ring-primary-500' : '']" :aria-pressed="filter === status" @click="filter = filter === status ? '' : status">{{ label }} {{ counts[status] }}</button>
    </div>
    <div class="flex flex-wrap items-center justify-between gap-3">
      <div class="flex flex-wrap gap-2">
        <button class="btn btn-primary" :disabled="props.loading || !(counts.pending + counts.waiting_verification) || !!editingId" @click="emit('collect')">{{ props.loading && counts.waiting_verification ? '等待验证…' : counts.running ? '正在采集…' : `开始采集（${counts.pending + counts.waiting_verification}）` }}</button>
        <button class="btn btn-outline" :disabled="props.loading || !counts.failed || !!editingId" @click="emit('collect', props.rows.filter((row) => row.status === 'failed').map((row) => row.id))">重试失败项</button>
      </div>
      <button class="btn btn-ghost text-sm" :disabled="props.loading || !counts.success || !!editingId" @click="emit('update', props.rows.filter((row) => row.status !== 'success'))">移除已完成</button>
    </div>
    <div v-if="!props.rows.length" class="rounded-2xl border border-dashed border-slate-300 px-6 py-12 text-center dark:border-dark-600">
      <p class="font-semibold">还没有待采集的链接</p>
      <p class="muted mt-2">在上方添加 URL，即可在这里管理链接并查看采集状态。</p>
    </div>
    <div v-else class="overflow-x-auto rounded-xl border border-slate-200 dark:border-dark-700">
      <table class="w-full min-w-[620px] text-left text-sm">
        <caption class="sr-only">批量采集链接、状态与操作</caption>
        <thead class="bg-slate-50 text-xs text-slate-500 dark:bg-dark-900 dark:text-accent-300">
          <tr><th scope="col" class="p-3">商品 / URL</th><th scope="col" class="w-28 p-3">状态</th><th scope="col" class="w-44 p-3">操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="row in visibleRows" :key="row.id" :data-row-id="row.id" class="border-t border-slate-200 align-top dark:border-dark-700">
            <td class="max-w-lg p-3">
              <form v-if="editingId === row.id" class="space-y-2" @submit.prevent="saveEdit">
                <input v-model="editingUrl" aria-label="编辑 URL" class="input text-sm" :disabled="props.loading" @keydown.esc="editingId = ''" />
                <div class="flex gap-2"><button class="btn btn-primary py-1 text-xs" :disabled="props.loading">保存</button><button type="button" class="btn btn-outline py-1 text-xs" @click="editingId = ''">取消</button></div>
              </form>
              <div v-else class="flex items-start gap-3">
                <img v-if="row.image" :src="row.image" alt="商品主图" class="size-12 shrink-0 rounded-lg object-cover" loading="lazy" />
                <div class="min-w-0">
                  <div v-if="row.title" class="mb-1 font-medium">{{ row.title }}</div>
                  <a :href="row.url" target="_blank" rel="noopener noreferrer" class="break-all text-primary-700 hover:underline dark:text-primary-300">{{ row.url }}</a>
                  <p v-if="row.platform" class="muted mt-1 text-xs">{{ row.platform }}</p>
                  <p v-if="row.error" class="mt-2 break-words text-xs text-rose-600 dark:text-rose-300">{{ row.error }}</p>
                  <p v-if="row.nextAction" class="muted mt-1 text-xs">{{ row.nextAction }}</p>
                </div>
              </div>
            </td>
            <td class="p-3"><span class="inline-flex whitespace-nowrap rounded-full px-2.5 py-1 text-xs font-medium" :class="badgeClasses[row.status]"><span v-if="row.status === 'running'" class="mr-1.5 self-center size-2 animate-pulse rounded-full bg-current" />{{ collectBatchStatusLabels[row.status] }}</span></td>
            <td class="p-3">
              <div class="flex flex-wrap gap-x-3 gap-y-2">
                <button class="text-primary-700 disabled:opacity-40 dark:text-primary-300" :disabled="props.loading || !!editingId" @click="editRow(row)">编辑</button>
                <button class="text-rose-600 disabled:opacity-40 dark:text-rose-300" :disabled="props.loading" @click="removeRow(row.id)">删除</button>
                <button v-if="row.status === 'failed'" class="text-primary-700 disabled:opacity-40 dark:text-primary-300" :disabled="props.loading || !!editingId" @click="emit('collect', [row.id])">重试</button>
              </div>
            </td>
          </tr>
          <tr v-if="!visibleRows.length"><td colspan="3" class="p-8 text-center text-slate-500">没有符合此状态的链接。</td></tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
