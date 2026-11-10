<script setup lang="ts">
import { computed, ref } from 'vue'
import { hasLocalOperations } from '@/utils/desktop'
import { launchDesktop, useDesktopHandoffContext, type DesktopWorkflowState } from '@/utils/desktopHandoff'
import PageHeader from '@/components/layout/PageHeader.vue'
import CollectBatchManager from './CollectBatchManager.vue'
import type { CollectBatchRow, CollectDiagnostics, CollectForm, Product } from '@/types/workflow'

const props = defineProps<{
  form: CollectForm
  diagnostics: CollectDiagnostics
  product: Product
  loading: boolean
  error: string
  batchRows: CollectBatchRow[]
}>()

const emit = defineEmits<{
  collect: []
  batchCollect: [rowIds?: string[]]
  updateBatchRows: [rows: CollectBatchRow[]]
  cancelVerification: []
  clearProduct: []
  saveSettings: []
}>()

const localOperations = hasLocalOperations()
const advancedOpen = ref(false)
function collectionHandoff(collectAction?: DesktopWorkflowState['collectAction']) {
  return {
    target: '/?tab=collect',
    state: { workflow: {
      collect: { mode: props.form.mode, productUrl: props.form.productUrl, platform: props.form.platform },
      batchRows: props.batchRows,
      ...(collectAction ? { collectAction } : {}),
    } },
  }
}
useDesktopHandoffContext(() => collectionHandoff())

const panelStyle = {
  titleClass: 'text-slate-950 dark:text-white',
  subtitleClass: 'text-slate-500 dark:text-accent-300',
  badgeClass: 'bg-primary-50 text-primary-700 ring-primary-200 dark:bg-primary-500/10 dark:text-primary-200 dark:ring-primary-500/30',
  panelClass: 'bg-slate-50 ring-slate-200 dark:bg-dark-900/80 dark:ring-dark-700',
  panelLabelClass: 'text-slate-500 dark:text-accent-300',
  panelValueClass: 'text-slate-950 dark:text-white',
}
const collectTabs = [
  { mode: 'browser', label: '浏览器采集' },
  { mode: 'api', label: 'API 采集' },
] as const

function navigateCollectTabs(event: KeyboardEvent) {
  if (props.loading || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
  event.preventDefault()
  let mode: CollectForm['mode'] = props.form.mode === 'browser' ? 'api' : 'browser'
  if (event.key === 'Home') mode = 'browser'
  if (event.key === 'End') mode = 'api'
  props.form.mode = mode
  document.getElementById(`collect-tab-${mode}`)?.focus()
}

const collectStatusLabel = computed(() => {
  if (props.diagnostics.status === 'success') return '采集完成'
  if (props.diagnostics.status === 'failed') return '采集失败'
  if (props.diagnostics.status === 'waiting_verification') return '等待人工验证'
  if (props.diagnostics.status === 'running') return '正在采集'
  return '未开始'
})
function openDebugFile(path: string) {
  if (path) window.open(`/file?path=${encodeURIComponent(path)}`, '_blank')
}
function copyDiagnostics() {
  void navigator.clipboard?.writeText(JSON.stringify(props.diagnostics.raw || {}, null, 2))
}
function collectBatch(rowIds?: string[]) {
  if (!localOperations) {
    void launchDesktop(collectionHandoff({ type: 'batch', ...(rowIds ? { rowIds } : {}) }))
    return
  }
  emit('batchCollect', rowIds)
}
</script>

<template>
  <div class="space-y-6">
    <PageHeader title="采集商品" description="输入商品链接，采集到采集箱。">
      <template #actions>
        <span class="rounded-full bg-primary-50 px-4 py-2 text-sm font-semibold text-primary-700 ring-1 ring-primary-100 dark:bg-primary-500/10 dark:text-primary-200 dark:ring-primary-500/30">{{ collectStatusLabel }}</span>
        <button class="btn btn-outline" :disabled="props.loading" @click="emit('clearProduct')">清空当前商品</button>
      </template>
    </PageHeader>
    <div v-if="props.diagnostics.status === 'waiting_verification'" role="status" class="flex flex-wrap items-center justify-between gap-4 rounded-xl border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-amber-200">
      <div><p class="font-semibold">正在等待你完成验证</p><p class="mt-1">请在采集浏览器的原商品页完成验证。完成后会自动采集这一项，再继续后面的链接。</p></div>
      <button class="btn btn-outline shrink-0" @click="emit('cancelVerification')">取消等待</button>
    </div>
    <div v-if="props.error" role="alert" class="rounded-2xl bg-rose-50 p-4 text-sm font-medium text-rose-700 ring-1 ring-rose-200">{{ props.error }}</div>
    <section class="grid gap-6 2xl:grid-cols-[minmax(0,1fr)_320px]">
      <main class="space-y-6">
        <section data-testid="collect-active-card" class="card space-y-5">
          <nav class="flex gap-1 border-b border-accent-200 dark:border-dark-700" role="tablist" aria-label="采集方式" @keydown="navigateCollectTabs">
            <button
              v-for="tab in collectTabs" :id="`collect-tab-${tab.mode}`" :key="tab.mode"
              type="button" role="tab" :aria-controls="`collect-panel-${tab.mode}`"
              :aria-selected="props.form.mode === tab.mode" :tabindex="props.form.mode === tab.mode ? 0 : -1"
              :disabled="props.loading" class="border-b-2 px-4 py-2.5 text-sm font-semibold"
              :class="props.form.mode === tab.mode ? 'border-primary-500 text-primary-700 dark:text-primary-200' : 'border-transparent text-accent-600 dark:text-accent-300'"
              @click="props.form.mode = tab.mode"
            >{{ tab.label }}</button>
          </nav>
          <form v-show="props.form.mode === 'api'" id="collect-panel-api" role="tabpanel" aria-labelledby="collect-tab-api" class="space-y-4" @submit.prevent="emit('collect')">
            <label class="block">
              <span class="text-sm font-semibold">1688 商品详情链接</span>
              <input v-model.trim="props.form.productUrl" type="url" required :disabled="props.loading" class="input mt-2" placeholder="https://detail.1688.com/offer/..." />
            </label>
            <p class="muted">使用已授权的 1688 采购账号读取商品详情。</p>
            <button class="btn btn-primary" :disabled="props.loading || !props.form.productUrl.trim()">开始 API 采集</button>
          </form>
          <section v-show="props.form.mode === 'browser'" id="collect-panel-browser" role="tabpanel" aria-labelledby="collect-tab-browser" class="space-y-4">
            <button type="button" class="btn btn-outline py-2 text-sm" :aria-expanded="advancedOpen" @click="advancedOpen = !advancedOpen">保存位置 {{ advancedOpen ? '−' : '+' }}</button>
            <div v-if="advancedOpen" class="space-y-4 rounded-xl border border-slate-200 bg-slate-50 p-4 dark:border-dark-700 dark:bg-dark-900">
              <label class="block"><span class="text-xs font-semibold">保存位置</span><input v-model="props.form.outputDir" :disabled="props.loading" class="input mt-1" placeholder="data/images/source" /></label>
              <button type="button" class="btn btn-outline" :disabled="props.loading" @click="emit('saveSettings')">保存设置</button>
            </div>
            <CollectBatchManager :rows="props.batchRows" :loading="props.loading" @update="emit('updateBatchRows', $event)" @collect="collectBatch" />
          </section>
        </section>
      </main>

      <aside class="space-y-6 min-w-0 2xl:sticky 2xl:top-6 2xl:self-start">
        <section data-testid="collect-diagnostics-card" class="card space-y-4">
          <div class="flex items-center justify-between gap-3">
            <div>
              <h3 class="text-base font-semibold" :class="panelStyle.titleClass">采集进度 / 诊断</h3>
              <p class="mt-1 text-sm" :class="panelStyle.subtitleClass">状态、错误码、调试截图和 HTML 快照。</p>
            </div>
            <span
              class="badge shrink-0 whitespace-nowrap"
              :class="{
                'bg-slate-100 text-slate-600 ring-1 ring-slate-200 dark:bg-dark-800 dark:text-accent-300 dark:ring-dark-600': props.diagnostics.status === 'idle',
                'bg-primary-50 text-primary-700 ring-1 ring-primary-200 dark:bg-primary-500/10 dark:text-primary-200 dark:ring-primary-500/30': ['running', 'waiting_verification'].includes(props.diagnostics.status),
                'bg-emerald-50 text-emerald-700 ring-1 ring-emerald-200 dark:bg-emerald-500/10 dark:text-emerald-200 dark:ring-emerald-500/30': props.diagnostics.status === 'success',
                'bg-rose-50 text-rose-700 ring-1 ring-rose-200 dark:bg-rose-500/10 dark:text-rose-200 dark:ring-rose-500/30': props.diagnostics.status === 'failed',
              }"
            >
              {{ collectStatusLabel }}
            </span>
          </div>

          <div>
            <div class="mb-2 flex justify-between text-sm font-medium" :class="panelStyle.subtitleClass">
              <span>{{ props.diagnostics.message }}</span>
              <span>{{ props.diagnostics.progress }}%</span>
            </div>
            <div class="h-2 rounded-full bg-white/70 ring-1 dark:bg-dark-900" :class="panelStyle.panelClass">
              <div class="h-2 rounded-full bg-brand-600 transition-all" :style="{ width: `${props.diagnostics.progress}%` }" />
            </div>
          </div>

          <dl class="grid grid-cols-2 gap-3 text-sm">
            <div class="col-span-2 rounded-2xl p-3 ring-1" :class="panelStyle.panelClass">
              <dt :class="panelStyle.panelLabelClass">图片数量</dt>
              <dd class="mt-1 text-xl font-bold" :class="panelStyle.panelValueClass">{{ props.diagnostics.downloadedImages }}</dd>
            </div>
            <div class="col-span-2 rounded-2xl p-3 ring-1" :class="panelStyle.panelClass">
              <dt :class="panelStyle.panelLabelClass">错误码</dt>
              <dd class="mt-1 break-all font-mono text-sm" :class="panelStyle.panelValueClass">{{ props.diagnostics.errorCode || '-' }}</dd>
            </div>
            <div class="col-span-2 rounded-2xl p-3 ring-1" :class="panelStyle.panelClass">
              <dt :class="panelStyle.panelLabelClass">来源</dt>
              <dd class="mt-1 break-all text-sm font-medium" :class="panelStyle.panelValueClass">{{ props.diagnostics.lastSourceUrl || '-' }}</dd>
            </div>
          </dl>

          <div v-if="props.diagnostics.nextAction" class="rounded-2xl bg-primary-50 p-3 text-sm text-primary-800 ring-1 ring-primary-200 dark:bg-primary-500/10 dark:text-primary-200 dark:ring-primary-500/30">
            下一步：{{ props.diagnostics.nextAction }}
          </div>
          <div v-if="props.diagnostics.antiBotWarning" class="rounded-2xl bg-amber-50 p-3 text-sm text-amber-800 ring-1 ring-amber-200 dark:bg-amber-500/10 dark:text-amber-200 dark:ring-amber-500/30">
            检测到安全验证或反爬，请在采集浏览器中完成验证后重试。
          </div>
          <div class="flex flex-wrap gap-2">
            <button class="btn btn-outline py-1.5" :disabled="!props.diagnostics.screenshotPath" @click="openDebugFile(props.diagnostics.screenshotPath)">打开截图</button>
            <button class="btn btn-outline py-1.5" :disabled="!props.diagnostics.htmlSnapshotPath" @click="openDebugFile(props.diagnostics.htmlSnapshotPath)">打开 HTML</button>
            <button class="btn btn-outline py-1.5" @click="copyDiagnostics">复制日志</button>
          </div>
        </section>

        <section data-testid="collect-result-card" class="card">
          <div class="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h3 class="text-base font-semibold" :class="panelStyle.titleClass">当前采集结果</h3>
              <p class="mt-1 text-sm" :class="panelStyle.subtitleClass">后续文案、图片、核价、上架都基于这份数据。</p>
            </div>
            <span class="rounded-full px-2.5 py-1 text-xs font-semibold ring-1" :class="panelStyle.badgeClass">{{ props.product.source.sourcePlatform || '未采集' }}</span>
          </div>
          <div class="mt-5 space-y-3">
            <div class="rounded-2xl p-4 ring-1" :class="panelStyle.panelClass">
              <p class="text-xs font-semibold" :class="panelStyle.panelLabelClass">商品标题</p>
              <p class="mt-2 text-base font-bold" :class="panelStyle.panelValueClass">{{ props.product.source.title || props.product.name || '待采集' }}</p>
            </div>
            <div class="grid gap-3 sm:grid-cols-2 xl:grid-cols-1 2xl:grid-cols-2">
              <div class="rounded-2xl p-4 ring-1" :class="panelStyle.panelClass">
                <p class="text-xs font-semibold" :class="panelStyle.panelLabelClass">价格</p>
                <p class="mt-2 text-base font-bold" :class="panelStyle.panelValueClass">{{ props.product.source.price || '-' }} {{ props.product.source.currency }}</p>
              </div>
              <div class="rounded-2xl p-4 ring-1" :class="panelStyle.panelClass">
                <p class="text-xs font-semibold" :class="panelStyle.panelLabelClass">规格</p>
                <p class="mt-2 text-sm font-semibold" :class="panelStyle.panelValueClass">{{ props.product.source.dimensions.lengthCm || '-' }} × {{ props.product.source.dimensions.widthCm || '-' }} × {{ props.product.source.dimensions.heightCm || '-' }} cm</p>
                <p class="mt-1 text-sm text-slate-600 dark:text-accent-300">{{ props.product.source.weightKg || '-' }} kg</p>
              </div>
            </div>
          </div>
        </section>
      </aside>
    </section>
  </div>
</template>
