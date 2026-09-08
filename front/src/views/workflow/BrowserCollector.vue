<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { BrowserCollectRow, BrowserDebugStatus } from '@/types/workflow'

const props = defineProps<{ status: BrowserDebugStatus | null; rows: BrowserCollectRow[]; loading: boolean }>()
const emit = defineEmits<{ open: []; check: []; profile: []; collect: [saveOnly: boolean, tabUrls: string[]] }>()
const selectedUrls = ref<string[]>([])
const tabs = computed(() => props.status?.connected
  ? [...new Map(props.status.tabs.filter((tab) => /^https?:\/\//i.test(tab.url)).map((tab) => [tab.url, tab])).values()]
  : [])
const selectedTabs = computed(() => tabs.value.filter((tab) => selectedUrls.value.includes(tab.url)))
const allSelected = computed(() => tabs.value.length > 0 && selectedTabs.value.length === tabs.value.length)
const succeeded = computed(() => props.rows.filter((row) => row.status === 'success').length)
const failed = computed(() => props.rows.filter((row) => row.status === 'failed').length)
const activeRow = computed(() => props.rows.find((row) => ['running', 'waiting_verification'].includes(row.status)))

watch(tabs, (next, previous) => {
  selectedUrls.value = selectedUrls.value.filter((url) => next.some((tab) => tab.url === url))
  if (!previous?.length && next.length === 1) selectedUrls.value = [next[0].url]
}, { immediate: true })

function toggleAll() {
  if (props.loading) return
  selectedUrls.value = allSelected.value ? [] : tabs.value.map((tab) => tab.url)
}

function collect(saveOnly: boolean) {
  if (props.loading || !selectedTabs.value.length) return
  emit('collect', saveOnly, selectedTabs.value.map((tab) => tab.url))
}

function rowStatusLabel(row: BrowserCollectRow) {
  if (row.status === 'waiting_verification') return props.loading ? '等待人工验证' : '已暂停，待验证'
  if (row.status === 'running') return row.saveOnly ? '正在保存' : '正在采集'
  if (row.status === 'success') return row.saveOnly ? '快照已保存' : '采集成功'
  return row.status === 'failed' ? '失败' : '等待处理'
}
</script>

<template>
  <section data-testid="collect-active-card" class="card space-y-5">
    <div class="flex flex-wrap items-start justify-between gap-3">
      <div><h3 class="card-title">浏览器采集</h3><p class="muted mt-1">在专用 Chrome 登录并打开商品详情页，可多选页面，按列表顺序逐个采集到商品库。</p></div>
      <span class="rounded-full px-3 py-1 text-xs font-medium" :class="props.status?.connected ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-200' : 'bg-slate-100 text-slate-600 dark:bg-dark-700 dark:text-accent-200'">{{ props.status ? props.status.connected ? '浏览器已连接' : '浏览器未连接' : '尚未检测' }}</span>
    </div>
    <div class="rounded-2xl border border-slate-200 bg-slate-50 p-4 dark:border-dark-700 dark:bg-dark-900/60">
      <div class="flex flex-wrap items-center justify-between gap-4">
        <div><p class="text-sm font-semibold">1. 打开浏览器，准备商品页面</p><p class="muted mt-1 text-xs">支持 1688 / Amazon。遇到登录或验证码时，请先在浏览器中完成。</p></div>
        <button class="btn btn-primary" :disabled="props.loading" @click="emit('open')">打开采集浏览器</button>
      </div>
    </div>
    <div>
      <div class="flex flex-wrap items-center justify-between gap-3">
        <div><h4 class="text-sm font-semibold">2. 选择要采集的页面（可多选）</h4><p class="muted mt-1 text-xs">请勾选商品详情页；打开、切换或关闭页面后，刷新此列表。</p></div>
        <button class="btn btn-outline" :disabled="props.loading" @click="emit('check')">{{ props.status ? '刷新标签页' : '检测浏览器' }}</button>
      </div>
      <div v-if="!tabs.length" class="mt-4 rounded-2xl border border-dashed border-slate-300 p-8 text-center dark:border-dark-600">
        <p class="text-sm font-medium">{{ props.status?.connected ? '没有可采集的网页' : '等待连接采集浏览器' }}</p>
        <p class="muted mt-2 text-xs">{{ props.status?.nextAction || '点击“打开采集浏览器”，登录并打开商品详情页，再检测浏览器。' }}</p>
      </div>
      <div v-if="tabs.length" class="mt-4 flex flex-wrap items-center justify-between gap-3 text-sm">
        <label class="flex cursor-pointer items-center gap-2">
          <input type="checkbox" :checked="allSelected" :indeterminate.prop="selectedTabs.length > 0 && !allSelected" :disabled="props.loading" @change="toggleAll" />
          全选页面
        </label>
        <span class="muted">已选 {{ selectedTabs.length }} / {{ tabs.length }} 页</span>
      </div>
      <fieldset v-if="tabs.length" class="mt-3 space-y-2">
        <legend class="sr-only">选择浏览器标签页</legend>
        <label v-for="tab in tabs" :key="tab.url" class="flex cursor-pointer items-start gap-3 rounded-xl border p-3 transition-colors" :class="selectedUrls.includes(tab.url) ? 'border-primary-400 bg-primary-50/60 dark:border-primary-500 dark:bg-primary-500/10' : 'border-slate-200 hover:bg-slate-50 dark:border-dark-700 dark:hover:bg-dark-900'">
          <input v-model="selectedUrls" type="checkbox" name="collect-browser-tab" :value="tab.url" :disabled="props.loading" :aria-label="tab.title || tab.url" class="mt-1" />
          <span class="min-w-0 flex-1"><span class="block text-sm font-medium">{{ tab.title || '未命名页面' }}</span><span class="mt-1 block break-all text-xs text-slate-500 dark:text-accent-300">{{ tab.url }}</span></span>
          <span class="text-xs text-slate-500 dark:text-accent-300">{{ tab.platformDetected === 'unknown' ? '其他' : tab.platformDetected }}</span>
        </label>
      </fieldset>
      <p v-if="tabs.length && !selectedTabs.length" class="mt-3 text-xs text-amber-700 dark:text-amber-300">请至少勾选一个商品详情页。</p>
    </div>
    <div class="flex flex-wrap items-center justify-between gap-4 border-t border-slate-200 pt-5 dark:border-dark-700">
      <div><h4 class="text-sm font-semibold">3. 依次采集所选页面</h4><p class="muted mt-1 text-xs">逐页处理，单页失败后继续；遇到验证码时等待完成验证，再继续采集。</p></div>
      <div class="flex flex-wrap gap-2">
        <button class="btn btn-primary" :disabled="props.loading || !selectedTabs.length" @click="collect(false)">采集所选页面<span v-if="selectedTabs.length">（{{ selectedTabs.length }}）</span></button>
        <button class="btn btn-outline" :disabled="props.loading || !selectedTabs.length" @click="collect(true)">保存 HTML 快照</button>
      </div>
    </div>
    <section v-if="props.rows.length" class="space-y-3 border-t border-slate-200 pt-5 dark:border-dark-700" aria-label="本轮处理结果">
      <div aria-live="polite" class="space-y-1">
        <h4 class="text-sm font-semibold">本轮处理：{{ succeeded + failed }} / {{ props.rows.length }} 页</h4>
        <p class="muted text-xs">成功 {{ succeeded }} 页 · 失败 {{ failed }} 页</p>
        <p v-if="activeRow" class="text-xs text-primary-700 dark:text-primary-200">{{ rowStatusLabel(activeRow) }}：{{ activeRow.title || activeRow.url }}</p>
      </div>
      <ol class="space-y-2">
        <li v-for="(row, index) in props.rows" :key="row.url" class="rounded-xl border border-slate-200 p-3 dark:border-dark-700">
          <div class="flex items-start justify-between gap-3">
            <span class="min-w-0 break-words text-sm font-medium">{{ index + 1 }}. {{ row.title || row.url }}</span>
            <span class="shrink-0 text-xs" :class="row.status === 'failed' ? 'text-rose-600 dark:text-rose-300' : row.status === 'success' ? 'text-emerald-700 dark:text-emerald-300' : 'text-slate-500 dark:text-accent-300'">{{ rowStatusLabel(row) }}</span>
          </div>
          <p class="muted mt-1 break-all text-xs">{{ row.url }}</p>
          <p v-if="row.error" class="mt-2 text-xs text-rose-600 dark:text-rose-300">{{ row.error }}</p>
          <p v-if="row.nextAction" class="muted mt-1 text-xs">{{ row.nextAction }}</p>
          <a v-if="row.htmlSnapshotPath" class="mt-2 inline-block text-xs text-primary-700 underline dark:text-primary-200" :href="`/file?path=${encodeURIComponent(row.htmlSnapshotPath)}`" target="_blank" rel="noopener">打开 HTML 快照</a>
        </li>
      </ol>
    </section>
    <details class="text-xs text-slate-500 dark:text-accent-300">
      <summary class="cursor-pointer">连接详情与排查</summary>
      <div class="mt-3 space-y-2"><p>调试端口：{{ props.status?.port || 9222 }} · 网页：{{ tabs.length }}</p><p v-if="props.status?.errorMessage">{{ props.status.errorMessage }}</p><p v-if="props.status?.errorCode">错误码：{{ props.status.errorCode }}</p><button class="btn btn-outline py-1.5 text-xs" :disabled="props.loading" @click="emit('profile')">打开浏览器配置文件夹</button></div>
    </details>
  </section>
</template>
