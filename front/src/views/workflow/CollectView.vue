<script setup lang="ts">
import { computed, ref } from 'vue'
import PageHeader from '@/components/layout/PageHeader.vue'
import type { BrowserDebugStatus, CollectBatchRow, CollectDiagnostics, CollectForm, Product } from '@/types/workflow'

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
  collect: []
  batchCollect: []
  collectFromBrowser: [saveOnly: boolean]
  open1688Browser: []
  checkBrowser: []
  openProfile: []
  clearProduct: []
  saveSettings: []
  generateCopy: []
  importManual: []
  clean1688: []
}>()

type CollectTab = 'manual' | 'browser' | 'url'

const activeCollectTab = ref<CollectTab>('browser')
const advancedOpen = ref(false)

const collectTabs: Array<{
  key: CollectTab
  optionTitle: string
  title: string
  subtitle: string
  badge: string
  navClass: string
  labelClass: string
  selectClass: string
  summaryClass: string
  titleClass: string
  subtitleClass: string
  badgeClass: string
  panelClass: string
  panelLabelClass: string
  panelValueClass: string
  panelBorderClass: string
}> = [
  {
    key: 'browser',
    optionTitle: '方式一：浏览器采集',
    title: '浏览器采集',
    subtitle: '登录后从当前标签页采集',
    badge: '1688 / Amazon',
    navClass: 'bg-primary-50/80 ring-primary-100 dark:bg-primary-500/10 dark:ring-primary-500/20',
    labelClass: 'text-primary-700 dark:text-primary-200',
    selectClass: 'border-primary-200 bg-white focus:border-primary-500 focus:ring-primary-100 dark:border-primary-500/30 dark:bg-dark-900 dark:focus:ring-primary-500/20',
    summaryClass: 'bg-white ring-primary-100 dark:bg-dark-900/80 dark:ring-primary-500/20',
    titleClass: 'text-primary-900 dark:text-primary-100',
    subtitleClass: 'text-primary-700 dark:text-primary-200',
    badgeClass: 'bg-primary-50 text-primary-700 ring-primary-200 dark:bg-primary-500/10 dark:text-primary-200 dark:ring-primary-500/30',
    panelClass: 'bg-white ring-primary-100 dark:bg-dark-900/80 dark:ring-primary-500/20',
    panelLabelClass: 'text-primary-700 dark:text-primary-200',
    panelValueClass: 'text-slate-950 dark:text-white',
    panelBorderClass: 'border-primary-100 dark:border-primary-500/20',
  },
  {
    key: 'manual',
    optionTitle: '方式二：手动 / HTML 导入',
    title: '手动 / HTML 导入',
    subtitle: '粘贴资料或 HTML 导入',
    badge: '稳妥',
    navClass: 'bg-primary-50/80 ring-primary-100 dark:bg-primary-500/10 dark:ring-primary-500/20',
    labelClass: 'text-primary-700 dark:text-primary-200',
    selectClass: 'border-primary-200 bg-white focus:border-primary-500 focus:ring-primary-100 dark:border-primary-500/30 dark:bg-dark-900 dark:focus:ring-primary-500/20',
    summaryClass: 'bg-white ring-primary-100 dark:bg-dark-900/80 dark:ring-primary-500/20',
    titleClass: 'text-primary-900 dark:text-primary-100',
    subtitleClass: 'text-primary-700 dark:text-primary-200',
    badgeClass: 'bg-primary-50 text-primary-700 ring-primary-200 dark:bg-primary-500/10 dark:text-primary-200 dark:ring-primary-500/30',
    panelClass: 'bg-white ring-primary-100 dark:bg-dark-900/80 dark:ring-primary-500/20',
    panelLabelClass: 'text-primary-700 dark:text-primary-200',
    panelValueClass: 'text-slate-950 dark:text-white',
    panelBorderClass: 'border-primary-100 dark:border-primary-500/20',
  },
  {
    key: 'url',
    optionTitle: '方式三：URL / 批量采集',
    title: 'URL / 批量采集',
    subtitle: '自动抓取链接或列表',
    badge: '高级',
    navClass: 'bg-accent-50 ring-accent-200 dark:bg-dark-900/70 dark:ring-dark-700',
    labelClass: 'text-accent-600 dark:text-accent-300',
    selectClass: 'border-accent-300 bg-white focus:border-primary-500 focus:ring-primary-100 dark:border-dark-700 dark:bg-dark-900 dark:focus:ring-primary-500/20',
    summaryClass: 'bg-white ring-accent-200 dark:bg-dark-900/80 dark:ring-dark-700',
    titleClass: 'text-accent-950 dark:text-white',
    subtitleClass: 'text-accent-500 dark:text-accent-300',
    badgeClass: 'bg-accent-100 text-accent-600 ring-accent-200 dark:bg-dark-800 dark:text-accent-300 dark:ring-dark-600',
    panelClass: 'bg-white ring-accent-200 dark:bg-dark-900/80 dark:ring-dark-700',
    panelLabelClass: 'text-accent-600 dark:text-accent-300',
    panelValueClass: 'text-slate-950 dark:text-white',
    panelBorderClass: 'border-accent-200 dark:border-dark-700',
  },
]

const urlCollectModes = [
  { value: 'browser', label: '浏览器会话优先' },
  { value: 'http', label: 'HTTP 抓取' },
] as const

const collectStatusLabel = computed(() => {
  if (props.diagnostics.status === 'success') return '采集成功'
  if (props.diagnostics.status === 'failed') return '采集失败'
  if (props.diagnostics.status === 'running') return '采集中'
  return '等待采集'
})

const hasCollectedProduct = computed(() => Boolean(props.product.source.title || props.product.name))
const activeCollectTabMeta = computed(() => collectTabs.find((tab) => tab.key === activeCollectTab.value) || collectTabs[0])

function selectCollectTab(tab: CollectTab) {
  activeCollectTab.value = tab
  if (tab === 'manual' && props.form.mode !== 'extension') props.form.mode = 'manual'
  if (tab === 'browser') props.form.mode = 'browser'
  if (tab === 'url' && ['manual', 'extension'].includes(props.form.mode)) props.form.mode = 'browser'
}

function openDebugFile(path: string) {
  if (!path) return
  window.open(`/file?path=${encodeURIComponent(path)}`, '_blank')
}

function copyDiagnostics() {
  void navigator.clipboard?.writeText(JSON.stringify(props.diagnostics.raw || {}, null, 2))
}
</script>

<template>
  <div class="space-y-6">
    <PageHeader
      eyebrow="Collect / Source Only"
      title="采集商品"
      description="选择一种采集方式后继续操作。默认从“浏览器采集”开始，适合需要登录、验证码或当前标签页上下文的 1688 / Amazon 商品。"
    >
      <template #actions>
        <span class="rounded-full bg-primary-50 px-4 py-2 text-sm font-semibold text-primary-700 ring-1 ring-primary-100 dark:bg-primary-500/10 dark:text-primary-200 dark:ring-primary-500/30">{{ collectStatusLabel }}</span>
        <button class="btn btn-outline" :disabled="props.loading" @click="emit('clearProduct')">清空当前商品</button>
      </template>
    </PageHeader>

    <div v-if="props.error" class="rounded-2xl bg-rose-50 p-4 text-sm font-medium text-rose-700 ring-1 ring-rose-200">
      {{ props.error }}
    </div>

    <nav class="rounded-3xl p-4 shadow-soft ring-1" :class="activeCollectTabMeta.navClass">
      <div class="grid gap-4 lg:grid-cols-[minmax(240px,360px)_minmax(0,1fr)] lg:items-center">
        <label class="block">
          <span class="text-xs font-semibold uppercase tracking-[0.16em]" :class="activeCollectTabMeta.labelClass">采集方式</span>
          <select
            :value="activeCollectTab"
            class="input mt-2 text-base font-semibold"
            :class="activeCollectTabMeta.selectClass"
            @change="selectCollectTab(($event.target as HTMLSelectElement).value as CollectTab)"
          >
            <option v-for="tab in collectTabs" :key="tab.key" :value="tab.key">{{ tab.optionTitle }}</option>
          </select>
        </label>
        <div class="rounded-2xl p-4 ring-1" :class="activeCollectTabMeta.summaryClass">
          <div class="flex flex-wrap items-start justify-between gap-3">
            <div>
              <div class="text-base font-bold" :class="activeCollectTabMeta.titleClass">{{ activeCollectTabMeta.title }}</div>
              <div class="mt-1 text-sm" :class="activeCollectTabMeta.subtitleClass">{{ activeCollectTabMeta.subtitle }}</div>
            </div>
            <span class="rounded-full px-2.5 py-1 text-xs ring-1" :class="activeCollectTabMeta.badgeClass">{{ activeCollectTabMeta.badge }}</span>
          </div>
        </div>
      </div>
    </nav>

    <section class="grid gap-6 xl:grid-cols-[minmax(0,1fr)_380px]">
      <main class="space-y-6">
        <section v-if="activeCollectTab === 'manual'" class="card space-y-6">
          <div class="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h3 class="card-title">方式二：手动 / HTML 导入</h3>
              <p class="muted mt-1">适合第一次跑通、1688 触发验证、页面解析失败、或已经有商品资料的场景。</p>
            </div>
            <span class="badge-info">/api/collect-extension-payload</span>
          </div>

          <div class="grid gap-4 lg:grid-cols-3">
            <div class="rounded-2xl border border-emerald-100 bg-emerald-50 p-4">
              <div class="text-sm font-bold text-emerald-950">1. 选择来源与目标草稿</div>
              <div class="mt-4 space-y-4">
                <label class="block">
                  <span class="text-xs font-semibold text-emerald-800">来源平台</span>
                  <select v-model="props.form.platform" class="input mt-1 bg-white">
                    <option value="1688">1688</option>
                    <option value="amazon">Amazon</option>
                    <option value="manual">手动</option>
                    <option value="unknown">其他</option>
                  </select>
                </label>
                <div>
                  <span class="text-xs font-semibold text-emerald-800">认领到平台草稿</span>
                  <div class="mt-2 space-y-2 text-sm text-emerald-950">
                    <label class="flex items-center gap-2"><input v-model="props.form.selectedClaimPlatforms" type="checkbox" value="mercadolibre" /> Mercado Libre</label>
                    <label class="flex items-center gap-2"><input v-model="props.form.selectedClaimPlatforms" type="checkbox" value="wildberries" /> Wildberries</label>
                    <label class="flex items-center gap-2"><input v-model="props.form.selectedClaimPlatforms" type="checkbox" value="ozon" /> Ozon</label>
                  </div>
                </div>
              </div>
            </div>

            <div class="rounded-2xl border border-blue-100 bg-blue-50 p-4 lg:col-span-2">
              <div class="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <div class="text-sm font-bold text-blue-950">2. 可选：粘贴原始文本 / HTML</div>
                  <p class="mt-1 text-xs text-blue-800">如果是 1688 页面文本，先点“清洗 1688 文本”，系统会回填标题、价格、规格和图片。</p>
                </div>
                <button class="btn btn-secondary py-1.5" :disabled="props.loading || !props.form.rawText.trim()" @click="emit('clean1688')">清洗 1688 文本</button>
              </div>
              <div class="mt-4 grid gap-4 md:grid-cols-[0.9fr_1.1fr]">
                <label class="block">
                  <span class="text-xs font-semibold text-blue-800">来源链接，可选</span>
                  <input v-model="props.form.productUrl" class="input mt-1 bg-white" placeholder="https://detail.1688.com/offer/... 或 manual://..." />
                </label>
                <label class="block">
                  <span class="text-xs font-semibold text-blue-800">原始文本 / HTML</span>
                  <textarea v-model="props.form.rawText" class="input mt-1 min-h-28 bg-white font-mono" placeholder="粘贴 1688 文本、HTML、插件导出的原始内容；没有也可以直接填写下方字段。" />
                </label>
              </div>
            </div>
          </div>

          <div class="rounded-2xl border border-slate-200 bg-slate-50 p-4">
            <div class="text-sm font-bold text-slate-950">3. 核对并补齐商品字段</div>
            <div class="mt-4 grid gap-4 md:grid-cols-2">
              <label class="block"><span class="text-xs font-semibold text-slate-500">商品标题</span><input v-model="props.form.manualTitle" class="input mt-1 bg-white" placeholder="例如：可折叠收纳盒" /></label>
              <label class="block"><span class="text-xs font-semibold text-slate-500">识别价格</span><input v-model="props.form.manualPrice" class="input mt-1 bg-white" placeholder="12.5" /></label>
              <label class="block"><span class="text-xs font-semibold text-slate-500">尺寸</span><input v-model="props.form.manualDimensions" class="input mt-1 bg-white" placeholder="40 x 30 x 20 cm" /></label>
              <label class="block"><span class="text-xs font-semibold text-slate-500">重量 kg</span><input v-model="props.form.manualWeight" class="input mt-1 bg-white" placeholder="0.85" /></label>
            </div>
            <div class="mt-4 grid gap-4 lg:grid-cols-3">
              <label class="block"><span class="text-xs font-semibold text-slate-500">卖点，每行一个</span><textarea v-model="props.form.manualBullets" class="input mt-1 min-h-28 bg-white" /></label>
              <label class="block"><span class="text-xs font-semibold text-slate-500">描述</span><textarea v-model="props.form.manualDescription" class="input mt-1 min-h-28 bg-white" /></label>
              <label class="block"><span class="text-xs font-semibold text-slate-500">图片地址，每行一个</span><textarea v-model="props.form.manualImages" class="input mt-1 min-h-28 bg-white font-mono" placeholder="https://...jpg" /></label>
            </div>
          </div>

          <div class="rounded-2xl border border-slate-200 bg-white p-4">
            <div class="flex flex-wrap items-center justify-between gap-3">
              <div>
                <div class="text-sm font-bold text-slate-950">4. 导入商品库</div>
                <p class="mt-1 text-xs text-slate-500">导入后会生成商品记录、来源图片和目标平台草稿。</p>
              </div>
              <div class="flex flex-wrap gap-2">
                <button class="btn btn-primary" :disabled="props.loading" @click="emit('importManual')">导入手动内容</button>
                <button class="btn btn-outline" :disabled="props.loading || !hasCollectedProduct" @click="emit('generateCopy')">生成 AI 文案</button>
              </div>
            </div>
          </div>
        </section>

        <section v-else-if="activeCollectTab === 'browser'" class="card space-y-6 border-primary-100 bg-primary-50/70 dark:border-primary-500/20 dark:bg-primary-500/10">
          <div class="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h3 class="card-title text-primary-900 dark:text-primary-100">方式一：浏览器采集</h3>
              <p class="mt-1 text-sm text-primary-700 dark:text-primary-200">适合 1688 / Amazon 需要登录、验证码、滑块或反爬的页面。先在专用 Chrome 人工打开商品页，再从当前标签采集。</p>
            </div>
            <span class="rounded-full bg-white px-3 py-1 text-xs text-primary-700 ring-1 ring-primary-100 dark:bg-dark-900 dark:text-primary-200 dark:ring-primary-500/20">Remote Debugging {{ props.browserStatus?.port || 9222 }}</span>
          </div>

          <div class="grid gap-4 lg:grid-cols-2">
            <div class="rounded-2xl bg-white p-4 ring-1 ring-primary-100 dark:bg-dark-900/80 dark:ring-primary-500/20">
              <div class="text-sm font-bold text-slate-950 dark:text-white">1. 启动专用 Chrome</div>
              <p class="mt-2 text-sm text-slate-600 dark:text-accent-300">点击后端自动打开浏览器；如果失败，可以打开 Profile 文件夹检查环境。</p>
              <div class="mt-4 flex flex-wrap gap-2">
                <button class="btn btn-primary py-1.5" :disabled="props.loading" @click="emit('open1688Browser')">打开 1688 浏览器会话</button>
                <button class="btn btn-outline py-1.5" :disabled="props.loading" @click="emit('openProfile')">打开 Profile 文件夹</button>
              </div>
            </div>

            <div class="rounded-2xl bg-white p-4 ring-1 ring-primary-100 dark:bg-dark-900/80 dark:ring-primary-500/20">
              <div class="text-sm font-bold text-slate-950 dark:text-white">2. 登录并打开商品详情页</div>
              <p class="mt-2 text-sm text-slate-600 dark:text-accent-300">在专用 Chrome 完成登录、滑块 / 验证码，然后打开真实商品详情页。</p>
              <div class="mt-4 grid gap-4 md:grid-cols-2">
                <label class="block">
                  <span class="text-xs font-semibold text-slate-500 dark:text-accent-300">平台提示</span>
                  <select v-model="props.form.platform" class="input mt-1">
                    <option value="1688">1688</option>
                    <option value="amazon">Amazon</option>
                    <option value="unknown">其他</option>
                  </select>
                </label>
                <label class="block">
                  <span class="text-xs font-semibold text-slate-500 dark:text-accent-300">商品链接，可选</span>
                  <input v-model="props.form.productUrl" class="input mt-1" placeholder="用于辅助匹配标签页" />
                </label>
              </div>
            </div>
          </div>

          <div class="rounded-2xl bg-white p-4 ring-1 ring-primary-100 dark:bg-dark-900/80 dark:ring-primary-500/20">
            <div class="flex flex-wrap items-center justify-between gap-3">
              <div>
                <div class="text-sm font-bold text-slate-950 dark:text-white">3. 检测浏览器标签页</div>
                <p class="mt-1 text-xs text-slate-500 dark:text-accent-300">确认后端能连接 Chrome，并能看到 1688 / Amazon 商品页。</p>
              </div>
              <button class="btn btn-outline py-1.5" :disabled="props.loading" @click="emit('checkBrowser')">检测浏览器页面</button>
            </div>

            <div class="mt-4 rounded-2xl border border-slate-200 bg-slate-50 p-4 text-sm text-slate-700 dark:border-dark-700 dark:bg-dark-800 dark:text-accent-200">
              <div class="flex flex-wrap items-start justify-between gap-2">
                <div>
                  <div class="font-semibold dark:text-white">浏览器连接状态</div>
                  <div class="mt-1 text-xs text-slate-500 dark:text-accent-300">端口：{{ props.browserStatus?.port || 9222 }} / 标签页：{{ props.browserStatus?.tabsCount ?? 0 }}</div>
                </div>
                <span class="badge" :class="props.browserStatus?.connected ? 'bg-emerald-50 text-emerald-700 ring-1 ring-emerald-200' : 'bg-slate-100 text-slate-600 ring-1 ring-slate-200'">
                  {{ props.browserStatus?.connected ? '已连接' : '未连接' }}
                </span>
              </div>
              <div v-if="props.browserStatus" class="mt-3 rounded bg-white p-3 text-xs ring-1 ring-slate-200 dark:bg-dark-900 dark:ring-dark-700">
                <div>错误码：<span class="font-mono">{{ props.browserStatus.errorCode || '-' }}</span></div>
                <div class="mt-1">下一步：{{ props.browserStatus.nextAction || '-' }}</div>
                <div v-if="props.browserStatus.errorMessage" class="mt-1 text-slate-500 dark:text-accent-300">{{ props.browserStatus.errorMessage }}</div>
              </div>
              <div v-if="props.browserStatus?.tabs.length" class="mt-3 overflow-auto">
                <table class="w-full text-left text-xs">
                  <thead class="text-slate-500"><tr><th class="p-2">平台</th><th class="p-2">标题</th><th class="p-2">URL</th></tr></thead>
                  <tbody>
                    <tr v-for="tab in props.browserStatus.tabs" :key="tab.url" class="border-t">
                      <td class="p-2">{{ tab.platformDetected || '-' }}</td>
                      <td class="p-2">{{ tab.title || '-' }}</td>
                      <td class="max-w-md truncate p-2">{{ tab.url }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
          </div>

          <div class="rounded-2xl bg-white p-4 ring-1 ring-primary-100 dark:bg-dark-900/80 dark:ring-primary-500/20">
            <div class="flex flex-wrap items-center justify-between gap-3">
              <div>
                <div class="text-sm font-bold text-slate-950 dark:text-white">4. 从当前标签页采集</div>
                <p class="mt-1 text-xs text-slate-500 dark:text-accent-300">采集成功会写入商品库；失败时可先保存 HTML 快照用于排查或手动导入。</p>
              </div>
              <div class="flex flex-wrap gap-2">
                <button class="btn btn-primary" :disabled="props.loading" @click="emit('collectFromBrowser', false)">从当前标签页采集</button>
                <button class="btn btn-outline" :disabled="props.loading" @click="emit('collectFromBrowser', true)">保存 HTML 快照</button>
              </div>
            </div>
          </div>
        </section>

        <section v-else class="card space-y-6">
          <div class="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h3 class="card-title">方式三：URL / 批量采集</h3>
              <p class="muted mt-1">适合页面可公开访问或已准备 Cookie 的商品链接。1688 / Amazon 遇到验证时建议切到“浏览器标签采集”。</p>
            </div>
            <span class="badge-muted">/api/collect-source / /api/collect-batch</span>
          </div>

          <div class="rounded-2xl border border-slate-200 bg-slate-50 p-4">
            <div class="text-sm font-bold text-slate-950">1. 填写单链接或多链接</div>
            <div class="mt-4 grid gap-4 xl:grid-cols-2">
              <label class="block">
                <span class="text-xs font-semibold text-slate-500">单个商品链接</span>
                <div class="mt-1 flex flex-col gap-2 sm:flex-row">
                  <input v-model="props.form.productUrl" class="input min-w-0 bg-white" placeholder="https://detail.1688.com/offer/..." />
                  <button class="btn btn-primary shrink-0" :disabled="props.loading" @click="emit('collect')">采集单链接</button>
                </div>
              </label>
              <label class="block">
                <span class="text-xs font-semibold text-slate-500">多链接采集，每行一个</span>
                <textarea v-model="props.form.productUrls" class="input mt-1 min-h-24 bg-white" placeholder="Amazon / 1688 / 其他商品链接" />
              </label>
            </div>
          </div>

          <div class="rounded-2xl border border-slate-200 bg-white p-4">
            <div class="text-sm font-bold text-slate-950">2. 设置采集参数</div>
            <div class="mt-4 grid gap-4 md:grid-cols-3">
              <label class="block">
                <span class="text-xs font-semibold text-slate-500">来源平台</span>
                <select v-model="props.form.platform" class="input mt-1">
                  <option value="1688">1688</option>
                  <option value="amazon">Amazon</option>
                  <option value="unknown">其他 / 自动识别</option>
                </select>
              </label>
              <label class="block">
                <span class="text-xs font-semibold text-slate-500">采集模式</span>
                <select v-model="props.form.mode" class="input mt-1">
                  <option v-for="mode in urlCollectModes" :key="mode.value" :value="mode.value">{{ mode.label }}</option>
                </select>
              </label>
              <label class="flex items-end gap-2 rounded-2xl bg-slate-50 px-3 py-2 ring-1 ring-slate-200">
                <input v-model="props.form.autoAiRecognition" type="checkbox" class="size-4 rounded border-slate-300 text-brand-600 focus:ring-brand-500" />
                <span class="text-sm font-medium text-slate-700">采集后提示进入 AI 文案</span>
              </label>
            </div>

            <div class="mt-4 rounded-2xl border border-slate-200 bg-slate-50 p-4">
              <button type="button" class="flex w-full items-center justify-between text-left" @click="advancedOpen = !advancedOpen">
                <span>
                  <span class="block text-sm font-semibold text-slate-950">高级选项：Cookie / 保存位置 / 认领平台</span>
                  <span class="mt-1 block text-xs text-slate-500">只有遇到登录、验证码、反爬或需要保存默认配置时再展开。</span>
                </span>
                <span class="text-xl">{{ advancedOpen ? '−' : '+' }}</span>
              </button>
              <div v-if="advancedOpen" class="mt-4 space-y-4">
                <label class="block">
                  <span class="text-xs font-semibold text-slate-500">1688 Cookie</span>
                  <textarea v-model="props.form.alibabaCookie" class="input mt-1 min-h-24 bg-white font-mono" placeholder="复制浏览器请求 Cookie" />
                </label>
                <div class="grid gap-4 lg:grid-cols-2">
                  <label class="block">
                    <span class="text-xs font-semibold text-slate-500">保存位置</span>
                    <input v-model="props.form.outputDir" class="input mt-1 bg-white" placeholder="data/images/source" />
                  </label>
                  <div>
                    <span class="text-xs font-semibold text-slate-500">认领到平台草稿</span>
                    <div class="mt-2 flex flex-wrap gap-3 text-sm">
                      <label class="flex items-center gap-2"><input v-model="props.form.selectedClaimPlatforms" type="checkbox" value="mercadolibre" /> Mercado Libre</label>
                      <label class="flex items-center gap-2"><input v-model="props.form.selectedClaimPlatforms" type="checkbox" value="wildberries" /> Wildberries</label>
                      <label class="flex items-center gap-2"><input v-model="props.form.selectedClaimPlatforms" type="checkbox" value="ozon" /> Ozon</label>
                    </div>
                  </div>
                </div>
                <button type="button" class="btn btn-outline" :disabled="props.loading" @click="emit('saveSettings')">保存 Cookie / 设置</button>
              </div>
            </div>
          </div>

          <div class="rounded-2xl border border-slate-200 bg-white p-4">
            <div class="flex flex-wrap items-center justify-between gap-3">
              <div>
                <div class="text-sm font-bold text-slate-950">3. 开始采集</div>
                <p class="mt-1 text-xs text-slate-500">单链接写入当前商品；批量会逐条保存到商品库。</p>
              </div>
              <div class="flex flex-wrap gap-2">
                <button class="btn btn-primary" :disabled="props.loading" @click="emit('collect')">采集单链接</button>
                <button class="btn btn-secondary" :disabled="props.loading" @click="emit('batchCollect')">批量采集并保存</button>
              </div>
            </div>
          </div>

          <section v-if="props.batchRows.length" class="rounded-2xl border border-slate-200 bg-white p-4">
            <div class="flex items-center justify-between gap-3">
              <div>
                <h3 class="text-sm font-bold text-slate-950">批量采集结果</h3>
                <p class="muted mt-1">可回看成功、部分成功和失败原因。</p>
              </div>
              <span class="badge-info">{{ props.batchRows.length }} 条</span>
            </div>
            <div class="mt-4 overflow-auto rounded-2xl border border-slate-200">
              <table class="w-full text-left text-sm">
                <thead class="bg-slate-50 text-xs text-slate-500">
                  <tr><th class="p-3">主图</th><th class="p-3">来源</th><th class="p-3">链接</th><th class="p-3">标题</th><th class="p-3">状态</th><th class="p-3">错误码</th><th class="p-3">下一步建议</th></tr>
                </thead>
                <tbody>
                  <tr v-for="row in props.batchRows" :key="row.url" class="border-t">
                    <td class="p-3"><img v-if="row.image" :src="row.image" class="size-12 rounded object-cover" /><div v-else class="size-12 rounded bg-slate-100 text-center text-[10px] leading-[48px] text-slate-500">无图</div></td>
                    <td class="p-3">{{ row.platform || '-' }}</td>
                    <td class="max-w-xs truncate p-3">{{ row.url }}</td>
                    <td class="p-3">{{ row.title || '-' }}</td>
                    <td class="p-3"><span class="badge-muted">{{ row.status || '-' }}</span></td>
                    <td class="p-3 font-mono">{{ row.errorCode || row.error || '-' }}</td>
                    <td class="max-w-sm p-3">{{ row.nextAction || '-' }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </section>
        </section>
      </main>

      <aside class="space-y-6 xl:sticky xl:top-6 xl:self-start">
        <section class="rounded-2xl border p-5 shadow-card" :class="[activeCollectTabMeta.navClass, activeCollectTabMeta.panelBorderClass]">
          <div class="flex items-center justify-between gap-3">
            <div>
              <h3 class="text-base font-semibold" :class="activeCollectTabMeta.titleClass">采集进度 / 诊断</h3>
              <p class="mt-1 text-sm" :class="activeCollectTabMeta.subtitleClass">状态、错误码、调试截图和 HTML 快照。</p>
            </div>
            <span
              class="badge"
              :class="{
                'bg-slate-100 text-slate-600 ring-1 ring-slate-200 dark:bg-dark-800 dark:text-accent-300 dark:ring-dark-600': props.diagnostics.status === 'idle',
                'bg-primary-50 text-primary-700 ring-1 ring-primary-200 dark:bg-primary-500/10 dark:text-primary-200 dark:ring-primary-500/30': props.diagnostics.status === 'running',
                'bg-emerald-50 text-emerald-700 ring-1 ring-emerald-200 dark:bg-emerald-500/10 dark:text-emerald-200 dark:ring-emerald-500/30': props.diagnostics.status === 'success',
                'bg-rose-50 text-rose-700 ring-1 ring-rose-200 dark:bg-rose-500/10 dark:text-rose-200 dark:ring-rose-500/30': props.diagnostics.status === 'failed',
              }"
            >
              {{ props.diagnostics.status }}
            </span>
          </div>

          <div>
            <div class="mb-2 flex justify-between text-sm font-medium" :class="activeCollectTabMeta.subtitleClass">
              <span>{{ props.diagnostics.message }}</span>
              <span>{{ props.diagnostics.progress }}%</span>
            </div>
            <div class="h-2 rounded-full bg-white/70 ring-1 dark:bg-dark-900" :class="activeCollectTabMeta.panelClass">
              <div class="h-2 rounded-full bg-brand-600 transition-all" :style="{ width: `${props.diagnostics.progress}%` }" />
            </div>
          </div>

          <dl class="grid grid-cols-2 gap-3 text-sm">
            <div class="rounded-2xl p-3 ring-1" :class="activeCollectTabMeta.panelClass">
              <dt :class="activeCollectTabMeta.panelLabelClass">图片数量</dt>
              <dd class="mt-1 text-xl font-bold" :class="activeCollectTabMeta.panelValueClass">{{ props.diagnostics.downloadedImages }}</dd>
            </div>
            <div class="rounded-2xl p-3 ring-1" :class="activeCollectTabMeta.panelClass">
              <dt :class="activeCollectTabMeta.panelLabelClass">卖点数量</dt>
              <dd class="mt-1 text-xl font-bold" :class="activeCollectTabMeta.panelValueClass">{{ props.diagnostics.extractedBullets }}</dd>
            </div>
            <div class="col-span-2 rounded-2xl p-3 ring-1" :class="activeCollectTabMeta.panelClass">
              <dt :class="activeCollectTabMeta.panelLabelClass">错误码</dt>
              <dd class="mt-1 break-all font-mono text-sm" :class="activeCollectTabMeta.panelValueClass">{{ props.diagnostics.errorCode || '-' }}</dd>
            </div>
            <div class="col-span-2 rounded-2xl p-3 ring-1" :class="activeCollectTabMeta.panelClass">
              <dt :class="activeCollectTabMeta.panelLabelClass">来源</dt>
              <dd class="mt-1 break-all text-sm font-medium" :class="activeCollectTabMeta.panelValueClass">{{ props.diagnostics.lastSourceUrl || '-' }}</dd>
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

        <section class="rounded-2xl border p-5 shadow-card" :class="[activeCollectTabMeta.navClass, activeCollectTabMeta.panelBorderClass]">
          <div class="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h3 class="text-base font-semibold" :class="activeCollectTabMeta.titleClass">当前采集结果</h3>
              <p class="mt-1 text-sm" :class="activeCollectTabMeta.subtitleClass">后续文案、图片、核价、上架都基于这份数据。</p>
            </div>
            <span class="rounded-full px-2.5 py-1 text-xs font-semibold ring-1" :class="activeCollectTabMeta.badgeClass">{{ props.product.source.sourcePlatform || '未采集' }}</span>
          </div>
          <div class="mt-5 space-y-3">
            <div class="rounded-2xl p-4 ring-1" :class="activeCollectTabMeta.panelClass">
              <p class="text-xs font-semibold" :class="activeCollectTabMeta.panelLabelClass">商品标题</p>
              <p class="mt-2 text-base font-bold" :class="activeCollectTabMeta.panelValueClass">{{ props.product.source.title || props.product.name || '待采集' }}</p>
              <p class="mt-2 line-clamp-4 text-sm leading-6 text-slate-600 dark:text-accent-300">{{ props.product.source.description || '暂无描述' }}</p>
            </div>
            <div class="grid gap-3 sm:grid-cols-2 xl:grid-cols-1 2xl:grid-cols-2">
              <div class="rounded-2xl p-4 ring-1" :class="activeCollectTabMeta.panelClass">
                <p class="text-xs font-semibold" :class="activeCollectTabMeta.panelLabelClass">价格</p>
                <p class="mt-2 text-base font-bold" :class="activeCollectTabMeta.panelValueClass">{{ props.product.source.price || '-' }} {{ props.product.source.currency }}</p>
              </div>
              <div class="rounded-2xl p-4 ring-1" :class="activeCollectTabMeta.panelClass">
                <p class="text-xs font-semibold" :class="activeCollectTabMeta.panelLabelClass">规格</p>
                <p class="mt-2 text-sm font-semibold" :class="activeCollectTabMeta.panelValueClass">{{ props.product.source.dimensions.lengthCm || '-' }} × {{ props.product.source.dimensions.widthCm || '-' }} × {{ props.product.source.dimensions.heightCm || '-' }} cm</p>
                <p class="mt-1 text-sm text-slate-600 dark:text-accent-300">{{ props.product.source.weightKg || '-' }} kg</p>
              </div>
            </div>
          </div>
        </section>
      </aside>
    </section>
  </div>
</template>
