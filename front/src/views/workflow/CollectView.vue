<script setup lang="ts">
import { computed, ref } from 'vue'
import PageHeader from '@/components/layout/PageHeader.vue'
import CollectBatchManager from './CollectBatchManager.vue'
import BrowserCollector from './BrowserCollector.vue'
import type { BrowserDebugStatus, CollectBatchRow, CollectDiagnostics, CollectForm, Product, TransientCollectCredentials } from '@/types/workflow'

const props = defineProps<{
  form: CollectForm
  diagnostics: CollectDiagnostics
  product: Product
  loading: boolean
  error: string
  batchRows: CollectBatchRow[]
  browserStatus: BrowserDebugStatus | null
}>()

const emit = defineEmits<{
  collect: [credentials?: TransientCollectCredentials]
  batchCollect: [credentials?: TransientCollectCredentials, rowIds?: string[]]
  updateBatchRows: [rows: CollectBatchRow[]]
  cancelVerification: []
  collectFromBrowser: [saveOnly: boolean, tabUrl: string]
  open1688Browser: []
  checkBrowser: []
  openProfile: []
  clearProduct: []
  saveSettings: [credentials?: TransientCollectCredentials]
  importManual: []
  clean1688: []
}>()

type CollectTab = 'manual' | 'browser' | 'api' | 'url'

const activeCollectTab = ref<CollectTab>('browser')
const advancedOpen = ref(false)
const transientAlibabaCookie = ref('')

const collectTabs: Array<{ key: CollectTab; title: string; subtitle: string }> = [
  { key: 'browser', title: '浏览器采集', subtitle: '登录后选择商品页面' },
  { key: 'url', title: 'URL / 批量采集', subtitle: '管理链接并逐条采集' },
  { key: 'manual', title: '手动 / HTML 导入', subtitle: '粘贴已有商品资料' },
  { key: 'api', title: 'API 采集', subtitle: '使用已授权的 1688 API' },
]

const panelStyle = {
  titleClass: 'text-slate-950 dark:text-white',
  subtitleClass: 'text-slate-500 dark:text-accent-300',
  badgeClass: 'bg-primary-50 text-primary-700 ring-primary-200 dark:bg-primary-500/10 dark:text-primary-200 dark:ring-primary-500/30',
  panelClass: 'bg-slate-50 ring-slate-200 dark:bg-dark-900/80 dark:ring-dark-700',
  panelLabelClass: 'text-slate-500 dark:text-accent-300',
  panelValueClass: 'text-slate-950 dark:text-white',
}

const urlCollectModes = [
  { value: 'browser', label: '浏览器会话优先' },
  { value: 'http', label: 'HTTP 抓取' },
] as const

const collectStatusLabel = computed(() => {
  if (props.diagnostics.status === 'success') return '采集完成'
  if (props.diagnostics.status === 'failed') return '采集失败'
  if (props.diagnostics.status === 'waiting_verification') return '等待人工验证'
  if (props.diagnostics.status === 'running') return '正在采集'
  return '未开始'
})

function selectCollectTab(tab: CollectTab) {
  if (props.loading) return
  activeCollectTab.value = tab
  if (tab === 'manual' && props.form.mode !== 'extension') props.form.mode = 'manual'
  if (tab === 'browser') props.form.mode = 'browser'
  if (tab === 'api') {
    props.form.platform = '1688'
    props.form.mode = 'api'
  }
  if (tab === 'url') {
    if (['manual', 'extension', 'api'].includes(props.form.mode)) props.form.mode = 'browser'
    props.form.platform = 'unknown'
  }
}

function openDebugFile(path: string) {
  if (!path) return
  window.open(`/file?path=${encodeURIComponent(path)}`, '_blank')
}

function copyDiagnostics() {
  void navigator.clipboard?.writeText(JSON.stringify(props.diagnostics.raw || {}, null, 2))
}

function takeTransientCollectCredentials(): TransientCollectCredentials {
  const credentials = { alibabaCookie: transientAlibabaCookie.value.trim() }
  transientAlibabaCookie.value = ''
  return credentials
}

function collectProduct() {
  emit('collect', takeTransientCollectCredentials())
}

function collectBatch(rowIds?: string[]) {
  emit('batchCollect', takeTransientCollectCredentials(), rowIds)
}

function saveSettings() {
  emit('saveSettings', takeTransientCollectCredentials())
}
</script>

<template>
  <div class="space-y-6">
    <PageHeader
      eyebrow="商品来源"
      title="采集商品"
      description="从浏览器页面、商品链接或已有资料采集，统一保存到商品库。"
    >
      <template #actions>
        <span class="rounded-full bg-primary-50 px-4 py-2 text-sm font-semibold text-primary-700 ring-1 ring-primary-100 dark:bg-primary-500/10 dark:text-primary-200 dark:ring-primary-500/30">{{ collectStatusLabel }}</span>
        <button class="btn btn-outline" :disabled="props.loading" @click="emit('clearProduct')">清空当前商品</button>
      </template>
    </PageHeader>

    <div v-if="props.diagnostics.status === 'waiting_verification'" role="status" class="flex flex-wrap items-center justify-between gap-4 rounded-xl border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-amber-200">
      <div>
        <p class="font-semibold">正在等待你完成验证</p>
        <p class="mt-1">请在采集浏览器的原商品页完成验证。完成后会自动采集这一项，再继续后面的链接。</p>
      </div>
      <button class="btn btn-outline shrink-0" @click="emit('cancelVerification')">取消等待</button>
    </div>

    <div v-if="props.error" role="alert" class="rounded-2xl bg-rose-50 p-4 text-sm font-medium text-rose-700 ring-1 ring-rose-200">
      {{ props.error }}
    </div>

    <nav data-testid="collect-method-card" class="card p-4">
      <div class="grid grid-cols-2 gap-2 lg:grid-cols-4" aria-label="采集方式">
        <button v-for="tab in collectTabs" :key="tab.key" class="rounded-xl border p-4 text-left transition-colors disabled:opacity-50" :class="activeCollectTab === tab.key ? 'border-primary-400 bg-primary-50/60 dark:border-primary-500 dark:bg-primary-500/10' : 'border-transparent hover:bg-slate-50 dark:hover:bg-dark-900'" :aria-pressed="activeCollectTab === tab.key" :disabled="props.loading" :data-testid="`collect-method-${tab.key}`" @click="selectCollectTab(tab.key)">
          <span class="block text-sm font-semibold" :class="activeCollectTab === tab.key ? 'text-primary-800 dark:text-primary-200' : ''">{{ tab.title }}</span>
          <span class="muted mt-1 block text-xs">{{ tab.subtitle }}</span>
        </button>
      </div>
    </nav>

    <section class="grid gap-6 2xl:grid-cols-[minmax(0,1fr)_320px]">
      <main class="space-y-6">
        <section v-if="activeCollectTab === 'manual'" class="card space-y-6">
          <div class="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h3 class="card-title">手动 / HTML 导入</h3>
              <p class="muted mt-1">适合第一次跑通、1688 触发验证、页面解析失败、或已经有商品资料的场景。</p>
            </div>
            <span class="badge-info">资料导入</span>
          </div>

          <div class="grid gap-4 lg:grid-cols-3">
            <div class="rounded-2xl border border-emerald-100 bg-emerald-50 p-4 dark:border-emerald-500/20 dark:bg-emerald-500/10">
              <div class="text-sm font-bold text-emerald-950 dark:text-emerald-100">1. 选择来源</div>
              <div class="mt-4 space-y-4">
                <label class="block">
                  <span class="text-xs font-semibold text-emerald-800 dark:text-emerald-200">来源平台</span>
                  <select v-model="props.form.platform" class="input mt-1 bg-white">
                    <option value="1688">1688</option>
                    <option value="amazon">Amazon</option>
                    <option value="manual">手动</option>
                    <option value="unknown">其他</option>
                  </select>
                </label>
              </div>
            </div>

            <div class="rounded-2xl border border-blue-100 bg-blue-50 p-4 dark:border-blue-500/20 dark:bg-blue-500/10 lg:col-span-2">
              <div class="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <div class="text-sm font-bold text-blue-950 dark:text-blue-100">2. 可选：粘贴原始文本 / HTML</div>
                  <p class="mt-1 text-xs text-blue-800 dark:text-blue-200">如果是 1688 页面文本，先点“清洗 1688 文本”，系统会回填标题、价格、规格和图片。</p>
                </div>
                <button class="btn btn-secondary py-1.5" :disabled="props.loading || !props.form.rawText.trim()" @click="emit('clean1688')">清洗 1688 文本</button>
              </div>
              <div class="mt-4 grid gap-4 md:grid-cols-[0.9fr_1.1fr]">
                <label class="block">
                  <span class="text-xs font-semibold text-blue-800 dark:text-blue-200">来源链接，可选</span>
                  <input v-model="props.form.productUrl" class="input mt-1 bg-white" placeholder="https://detail.1688.com/offer/... 或 manual://..." />
                </label>
                <label class="block">
                  <span class="text-xs font-semibold text-blue-800 dark:text-blue-200">原始文本 / HTML</span>
                  <textarea v-model="props.form.rawText" class="input mt-1 min-h-28 bg-white font-mono" placeholder="粘贴 1688 文本、HTML、插件导出的原始内容；没有也可以直接填写下方字段。" />
                </label>
              </div>
            </div>
          </div>

          <div class="rounded-2xl border border-slate-200 bg-slate-50 p-4 dark:border-dark-700 dark:bg-dark-900">
            <div class="text-sm font-bold text-slate-950 dark:text-white">3. 核对并补齐商品字段</div>
            <div class="mt-4 grid gap-4 md:grid-cols-2">
              <label class="block"><span class="text-xs font-semibold text-slate-500 dark:text-accent-300">商品标题</span><input v-model="props.form.manualTitle" class="input mt-1 bg-white" placeholder="例如：可折叠收纳盒" /></label>
              <label class="block"><span class="text-xs font-semibold text-slate-500 dark:text-accent-300">识别价格</span><input v-model="props.form.manualPrice" class="input mt-1 bg-white" placeholder="12.5" /></label>
              <label class="block"><span class="text-xs font-semibold text-slate-500 dark:text-accent-300">尺寸</span><input v-model="props.form.manualDimensions" class="input mt-1 bg-white" placeholder="40 x 30 x 20 cm" /></label>
              <label class="block"><span class="text-xs font-semibold text-slate-500 dark:text-accent-300">重量 kg</span><input v-model="props.form.manualWeight" class="input mt-1 bg-white" placeholder="0.85" /></label>
            </div>
            <div class="mt-4 grid gap-4 lg:grid-cols-3">
              <label class="block"><span class="text-xs font-semibold text-slate-500 dark:text-accent-300">卖点，每行一个</span><textarea v-model="props.form.manualBullets" class="input mt-1 min-h-28 bg-white" /></label>
              <label class="block"><span class="text-xs font-semibold text-slate-500 dark:text-accent-300">描述</span><textarea v-model="props.form.manualDescription" class="input mt-1 min-h-28 bg-white" /></label>
              <label class="block"><span class="text-xs font-semibold text-slate-500 dark:text-accent-300">图片地址，每行一个</span><textarea v-model="props.form.manualImages" class="input mt-1 min-h-28 bg-white font-mono" placeholder="https://...jpg" /></label>
            </div>
          </div>

          <div class="rounded-2xl border border-slate-200 bg-white p-4 dark:border-dark-700 dark:bg-dark-900">
            <div class="flex flex-wrap items-center justify-between gap-3">
              <div>
                <div class="text-sm font-bold text-slate-950 dark:text-white">4. 导入商品库</div>
                <p class="mt-1 text-xs text-slate-500 dark:text-accent-300">导入后会生成商品记录和来源图片，可在商品库推到草稿箱。</p>
              </div>
              <div class="flex flex-wrap gap-2">
                <button class="btn btn-primary" :disabled="props.loading" @click="emit('importManual')">导入手动内容</button>
              </div>
            </div>
          </div>
        </section>

        <BrowserCollector v-else-if="activeCollectTab === 'browser'" :status="props.browserStatus" :loading="props.loading" @open="emit('open1688Browser')" @check="emit('checkBrowser')" @profile="emit('openProfile')" @collect="(saveOnly, tabUrl) => emit('collectFromBrowser', saveOnly, tabUrl)" />

        <section v-else-if="activeCollectTab === 'api'" data-testid="collect-active-card" class="card space-y-6">
          <div class="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h3 class="card-title text-blue-950 dark:text-blue-100">API 采集</h3>
              <p class="mt-1 text-sm text-blue-700 dark:text-blue-200">使用“平台授权”里的 1688 AppKey / Secret，从官方接口采集商品详情。</p>
            </div>
            <span class="rounded-full bg-white px-3 py-1 text-xs text-blue-700 ring-1 ring-blue-100 dark:bg-dark-900 dark:text-blue-200 dark:ring-blue-500/20">1688 API</span>
          </div>

          <div class="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
            <div class="rounded-2xl bg-white p-4 ring-1 ring-blue-100 dark:bg-dark-900/80 dark:ring-blue-500/20">
              <div class="text-sm font-bold text-slate-950 dark:text-white">填写 1688 商品链接</div>
              <label class="mt-4 block">
                <span class="text-xs font-semibold text-slate-500 dark:text-accent-300">商品详情链接</span>
                <input v-model="props.form.productUrl" class="input mt-1" placeholder="https://detail.1688.com/offer/..." />
              </label>
              <label class="mt-4 flex items-center gap-2 rounded-2xl bg-blue-50 px-3 py-2 ring-1 ring-blue-100 dark:bg-blue-500/10 dark:ring-blue-500/20">
                <input v-model="props.form.autoAiRecognition" type="checkbox" class="size-4 rounded border-blue-200 text-brand-600 focus:ring-brand-500" />
                <span class="text-sm font-medium text-blue-950 dark:text-blue-100">采集后提示检查商品库</span>
              </label>
            </div>

            <div class="rounded-2xl bg-white p-4 ring-1 ring-blue-100 dark:bg-dark-900/80 dark:ring-blue-500/20">
              <div class="text-sm font-bold text-slate-950 dark:text-white">2. 入库后处理</div>
              <p class="mt-3 text-sm text-slate-700 dark:text-accent-200">采集完成后先在商品库检查文本和通用图片，再推到草稿箱生成独立草稿。</p>
            </div>
          </div>

          <div class="rounded-2xl bg-white p-4 ring-1 ring-blue-100 dark:bg-dark-900/80 dark:ring-blue-500/20">
            <div class="flex flex-wrap items-center justify-between gap-3">
              <div>
                <div class="text-sm font-bold text-slate-950 dark:text-white">开始 API 采集</div>
                <p class="mt-1 text-xs text-slate-500 dark:text-accent-300">如果提示凭证缺失，请到“平台授权”保存 1688 采集 API。</p>
              </div>
              <button class="btn btn-primary" :disabled="props.loading" @click="collectProduct">API 采集单链接</button>
            </div>
          </div>
        </section>

        <section v-else class="card space-y-5">
          <div class="flex flex-wrap items-center justify-between gap-3">
            <label class="flex items-center gap-3 text-sm font-medium">
              采集模式
              <select v-model="props.form.mode" :disabled="props.loading" class="input w-auto">
                <option v-for="mode in urlCollectModes" :key="mode.value" :value="mode.value">{{ mode.label }}</option>
              </select>
            </label>
            <button type="button" class="btn btn-outline py-2 text-sm" :aria-expanded="advancedOpen" @click="advancedOpen = !advancedOpen">高级选项：Cookie / 保存位置 {{ advancedOpen ? '−' : '+' }}</button>
          </div>
          <div v-if="advancedOpen" class="space-y-4 rounded-xl border border-slate-200 bg-slate-50 p-4 dark:border-dark-700 dark:bg-dark-900 dark:border-dark-700 dark:bg-dark-900">
            <label class="block"><span class="text-xs font-semibold">1688 Cookie</span><textarea v-model="transientAlibabaCookie" :disabled="props.loading" data-testid="transient-alibaba-cookie" class="input mt-1 min-h-24 font-mono" placeholder="复制浏览器请求 Cookie；提交后立即清空" /></label>
            <label class="block"><span class="text-xs font-semibold">保存位置</span><input v-model="props.form.outputDir" :disabled="props.loading" class="input mt-1" placeholder="data/images/source" /></label>
            <button type="button" class="btn btn-outline" :disabled="props.loading" @click="saveSettings">保存 Cookie / 设置</button>
          </div>
          <CollectBatchManager :rows="props.batchRows" :loading="props.loading" @update="emit('updateBatchRows', $event)" @collect="collectBatch" />
          <details class="rounded-xl border border-slate-200 p-4 dark:border-dark-700">
            <summary class="cursor-pointer text-sm font-semibold">单链接快捷采集</summary>
            <div class="mt-4 space-y-3">
              <label class="block"><span class="text-xs font-semibold">来源平台</span><select v-model="props.form.platform" class="input mt-1" :disabled="props.loading"><option value="unknown">自动识别</option><option value="1688">1688</option><option value="amazon">Amazon</option></select></label>
              <label class="block"><span class="text-xs font-semibold">单个商品链接</span><input v-model="props.form.productUrl" :disabled="props.loading" class="input mt-1" placeholder="https://detail.1688.com/offer/..." /></label>
              <button class="btn btn-primary" :disabled="props.loading || !props.form.productUrl.trim()" @click="collectProduct">采集单链接</button>
            </div>
          </details>
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
            <div class="rounded-2xl p-3 ring-1" :class="panelStyle.panelClass">
              <dt :class="panelStyle.panelLabelClass">图片数量</dt>
              <dd class="mt-1 text-xl font-bold" :class="panelStyle.panelValueClass">{{ props.diagnostics.downloadedImages }}</dd>
            </div>
            <div class="rounded-2xl p-3 ring-1" :class="panelStyle.panelClass">
              <dt :class="panelStyle.panelLabelClass">卖点数量</dt>
              <dd class="mt-1 text-xl font-bold" :class="panelStyle.panelValueClass">{{ props.diagnostics.extractedBullets }}</dd>
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
            检测到安全验证或反爬，请登录浏览器会话、更新 Cookie 或改用当前标签页采集。
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
              <p class="mt-2 line-clamp-4 text-sm leading-6 text-slate-600 dark:text-accent-300">{{ props.product.source.description || '暂无描述' }}</p>
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
