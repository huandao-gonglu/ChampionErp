<script setup lang="ts">
import { computed, ref } from 'vue'
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
  chooseImages: [files: File[]]
  clearImages: []
  generateCopy: []
  importManual: []
  clean1688: []
}>()

type CollectTab = 'manual' | 'browser' | 'url'

const fileInput = ref<HTMLInputElement | null>(null)
const activeCollectTab = ref<CollectTab>('manual')
const advancedOpen = ref(false)

const collectTabs: Array<{ key: CollectTab; title: string; subtitle: string; badge: string }> = [
  { key: 'manual', title: '手动 / HTML 导入', subtitle: '最稳跑通，不依赖登录', badge: '推荐' },
  { key: 'browser', title: '浏览器标签采集', subtitle: '登录后从当前页采集', badge: '1688 / Amazon' },
  { key: 'url', title: 'URL / 批量采集', subtitle: '自动抓取链接或列表', badge: '高级' },
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

function selectCollectTab(tab: CollectTab) {
  activeCollectTab.value = tab
  if (tab === 'manual' && props.form.mode !== 'extension') props.form.mode = 'manual'
  if (tab === 'browser') props.form.mode = 'browser'
  if (tab === 'url' && ['manual', 'extension'].includes(props.form.mode)) props.form.mode = 'browser'
}

function openFilePicker() {
  fileInput.value?.click()
}

function onFilesSelected(event: Event) {
  const input = event.target as HTMLInputElement
  const files = Array.from(input.files || [])
  if (files.length) emit('chooseImages', files)
  input.value = ''
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
    <header class="rounded-3xl bg-slate-950 p-6 text-white shadow-soft">
      <div class="flex flex-col gap-5 xl:flex-row xl:items-end xl:justify-between">
        <div>
          <p class="text-xs font-semibold uppercase tracking-[0.22em] text-emerald-200">Collect / Source Only</p>
          <h2 class="mt-3 text-3xl font-bold">采集商品</h2>
          <p class="mt-3 max-w-3xl text-sm leading-6 text-slate-300">
            按采集方式拆成 3 个入口。新商品建议先用“手动 / HTML 导入”跑通，再处理 1688 / Amazon 登录采集。
          </p>
        </div>
        <div class="flex flex-wrap items-center gap-3">
          <span class="rounded-full bg-white/10 px-4 py-2 text-sm text-slate-200 ring-1 ring-white/15">{{ collectStatusLabel }}</span>
          <button class="btn border border-white/20 bg-white/10 text-white hover:bg-white/20" :disabled="props.loading" @click="emit('clearProduct')">清空当前商品</button>
        </div>
      </div>
    </header>

    <div v-if="props.error" class="rounded-2xl bg-rose-50 p-4 text-sm font-medium text-rose-700 ring-1 ring-rose-200">
      {{ props.error }}
    </div>

    <nav class="rounded-3xl bg-white p-2 shadow-soft ring-1 ring-slate-200">
      <div class="grid gap-2 lg:grid-cols-3">
        <button
          v-for="tab in collectTabs"
          :key="tab.key"
          type="button"
          class="rounded-2xl p-4 text-left transition"
          :class="activeCollectTab === tab.key ? 'bg-slate-950 text-white shadow-soft' : 'bg-slate-50 text-slate-700 hover:bg-slate-100'"
          @click="selectCollectTab(tab.key)"
        >
          <div class="flex items-start justify-between gap-3">
            <div>
              <div class="text-base font-bold">{{ tab.title }}</div>
              <div class="mt-1 text-sm" :class="activeCollectTab === tab.key ? 'text-slate-300' : 'text-slate-500'">{{ tab.subtitle }}</div>
            </div>
            <span class="rounded-full px-2.5 py-1 text-xs" :class="activeCollectTab === tab.key ? 'bg-white/15 text-white' : 'bg-white text-slate-500 ring-1 ring-slate-200'">{{ tab.badge }}</span>
          </div>
        </button>
      </div>
    </nav>

    <section class="grid gap-6 xl:grid-cols-[minmax(0,1fr)_380px]">
      <main class="space-y-6">
        <section v-if="activeCollectTab === 'manual'" class="card space-y-6">
          <div class="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h3 class="card-title">方式一：手动 / HTML 导入</h3>
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
                <p class="mt-1 text-xs text-slate-500">导入后会生成商品记录、图片池和目标平台草稿。</p>
              </div>
              <div class="flex flex-wrap gap-2">
                <button class="btn btn-primary" :disabled="props.loading" @click="emit('importManual')">导入手动内容</button>
                <button class="btn btn-outline" :disabled="props.loading || !hasCollectedProduct" @click="emit('generateCopy')">生成 AI 文案</button>
              </div>
            </div>
          </div>
        </section>

        <section v-else-if="activeCollectTab === 'browser'" class="card space-y-6 border-blue-100 bg-blue-50/70">
          <div class="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h3 class="card-title text-blue-950">方式二：浏览器标签采集</h3>
              <p class="mt-1 text-sm text-blue-800">适合 1688 / Amazon 需要登录、验证码、滑块或反爬的页面。先在专用 Chrome 人工打开商品页，再从当前标签采集。</p>
            </div>
            <span class="rounded-full bg-white px-3 py-1 text-xs text-blue-700 ring-1 ring-blue-100">Remote Debugging {{ props.browserStatus?.port || 9222 }}</span>
          </div>

          <div class="grid gap-4 lg:grid-cols-2">
            <div class="rounded-2xl bg-white p-4 ring-1 ring-blue-100">
              <div class="text-sm font-bold text-slate-950">1. 启动专用 Chrome</div>
              <p class="mt-2 text-sm text-slate-600">点击后端自动打开浏览器；如果失败，可以打开 Profile 文件夹检查环境。</p>
              <div class="mt-4 flex flex-wrap gap-2">
                <button class="btn btn-primary py-1.5" :disabled="props.loading" @click="emit('open1688Browser')">打开 1688 浏览器会话</button>
                <button class="btn btn-outline py-1.5" :disabled="props.loading" @click="emit('openProfile')">打开 Profile 文件夹</button>
              </div>
            </div>

            <div class="rounded-2xl bg-white p-4 ring-1 ring-blue-100">
              <div class="text-sm font-bold text-slate-950">2. 登录并打开商品详情页</div>
              <p class="mt-2 text-sm text-slate-600">在专用 Chrome 完成登录、滑块 / 验证码，然后打开真实商品详情页。</p>
              <div class="mt-4 grid gap-4 md:grid-cols-2">
                <label class="block">
                  <span class="text-xs font-semibold text-slate-500">平台提示</span>
                  <select v-model="props.form.platform" class="input mt-1">
                    <option value="1688">1688</option>
                    <option value="amazon">Amazon</option>
                    <option value="unknown">其他</option>
                  </select>
                </label>
                <label class="block">
                  <span class="text-xs font-semibold text-slate-500">商品链接，可选</span>
                  <input v-model="props.form.productUrl" class="input mt-1" placeholder="用于辅助匹配标签页" />
                </label>
              </div>
            </div>
          </div>

          <div class="rounded-2xl bg-white p-4 ring-1 ring-blue-100">
            <div class="flex flex-wrap items-center justify-between gap-3">
              <div>
                <div class="text-sm font-bold text-slate-950">3. 检测浏览器标签页</div>
                <p class="mt-1 text-xs text-slate-500">确认后端能连接 Chrome，并能看到 1688 / Amazon 商品页。</p>
              </div>
              <button class="btn btn-outline py-1.5" :disabled="props.loading" @click="emit('checkBrowser')">检测浏览器页面</button>
            </div>

            <div class="mt-4 rounded-2xl border border-slate-200 bg-slate-50 p-4 text-sm text-slate-700">
              <div class="flex flex-wrap items-start justify-between gap-2">
                <div>
                  <div class="font-semibold">浏览器连接状态</div>
                  <div class="mt-1 text-xs text-slate-500">端口：{{ props.browserStatus?.port || 9222 }} / 标签页：{{ props.browserStatus?.tabsCount ?? 0 }}</div>
                </div>
                <span class="badge" :class="props.browserStatus?.connected ? 'bg-emerald-50 text-emerald-700 ring-1 ring-emerald-200' : 'bg-slate-100 text-slate-600 ring-1 ring-slate-200'">
                  {{ props.browserStatus?.connected ? '已连接' : '未连接' }}
                </span>
              </div>
              <div v-if="props.browserStatus" class="mt-3 rounded bg-white p-3 text-xs ring-1 ring-slate-200">
                <div>错误码：<span class="font-mono">{{ props.browserStatus.errorCode || '-' }}</span></div>
                <div class="mt-1">下一步：{{ props.browserStatus.nextAction || '-' }}</div>
                <div v-if="props.browserStatus.errorMessage" class="mt-1 text-slate-500">{{ props.browserStatus.errorMessage }}</div>
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

          <div class="rounded-2xl bg-white p-4 ring-1 ring-blue-100">
            <div class="flex flex-wrap items-center justify-between gap-3">
              <div>
                <div class="text-sm font-bold text-slate-950">4. 从当前标签页采集</div>
                <p class="mt-1 text-xs text-slate-500">采集成功会写入商品库；失败时可先保存 HTML 快照用于排查或手动导入。</p>
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
                <div class="mt-1 flex gap-2">
                  <input v-model="props.form.productUrl" class="input bg-white" placeholder="https://detail.1688.com/offer/..." />
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
        <section class="card space-y-5">
          <div class="flex items-center justify-between gap-3">
            <div>
              <h3 class="card-title">采集进度 / 诊断</h3>
              <p class="muted mt-1">状态、错误码、调试截图和 HTML 快照。</p>
            </div>
            <span
              class="badge"
              :class="{
                'bg-slate-100 text-slate-600 ring-1 ring-slate-200': props.diagnostics.status === 'idle',
                'bg-blue-50 text-blue-700 ring-1 ring-blue-200': props.diagnostics.status === 'running',
                'bg-emerald-50 text-emerald-700 ring-1 ring-emerald-200': props.diagnostics.status === 'success',
                'bg-rose-50 text-rose-700 ring-1 ring-rose-200': props.diagnostics.status === 'failed',
              }"
            >
              {{ props.diagnostics.status }}
            </span>
          </div>

          <div>
            <div class="mb-2 flex justify-between text-sm font-medium text-slate-600">
              <span>{{ props.diagnostics.message }}</span>
              <span>{{ props.diagnostics.progress }}%</span>
            </div>
            <div class="h-2 rounded-full bg-slate-100">
              <div class="h-2 rounded-full bg-brand-600 transition-all" :style="{ width: `${props.diagnostics.progress}%` }" />
            </div>
          </div>

          <dl class="grid grid-cols-2 gap-3 text-sm">
            <div class="rounded-2xl bg-slate-50 p-3 ring-1 ring-slate-200">
              <dt class="text-slate-500">图片数量</dt>
              <dd class="mt-1 text-xl font-bold text-slate-950">{{ props.diagnostics.downloadedImages }}</dd>
            </div>
            <div class="rounded-2xl bg-slate-50 p-3 ring-1 ring-slate-200">
              <dt class="text-slate-500">卖点数量</dt>
              <dd class="mt-1 text-xl font-bold text-slate-950">{{ props.diagnostics.extractedBullets }}</dd>
            </div>
            <div class="rounded-2xl bg-slate-50 p-3 ring-1 ring-slate-200 col-span-2">
              <dt class="text-slate-500">错误码</dt>
              <dd class="mt-1 break-all font-mono text-sm text-slate-950">{{ props.diagnostics.errorCode || '-' }}</dd>
            </div>
            <div class="rounded-2xl bg-slate-50 p-3 ring-1 ring-slate-200 col-span-2">
              <dt class="text-slate-500">来源</dt>
              <dd class="mt-1 break-all text-sm font-medium text-slate-950">{{ props.diagnostics.lastSourceUrl || '-' }}</dd>
            </div>
          </dl>

          <div v-if="props.diagnostics.nextAction" class="rounded-2xl bg-blue-50 p-3 text-sm text-blue-800 ring-1 ring-blue-200">
            下一步：{{ props.diagnostics.nextAction }}
          </div>
          <div v-if="props.diagnostics.antiBotWarning" class="rounded-2xl bg-amber-50 p-3 text-sm text-amber-800 ring-1 ring-amber-200">
            检测到安全验证或反爬，请登录浏览器会话、更新 Cookie 或改用当前标签页采集。
          </div>
          <div class="flex flex-wrap gap-2">
            <button class="btn btn-outline py-1.5" :disabled="!props.diagnostics.screenshotPath" @click="openDebugFile(props.diagnostics.screenshotPath)">打开截图</button>
            <button class="btn btn-outline py-1.5" :disabled="!props.diagnostics.htmlSnapshotPath" @click="openDebugFile(props.diagnostics.htmlSnapshotPath)">打开 HTML</button>
            <button class="btn btn-outline py-1.5" @click="copyDiagnostics">复制日志</button>
          </div>
        </section>

        <section class="card">
          <div class="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h3 class="card-title">当前采集结果</h3>
              <p class="muted mt-1">后续文案、图片、核价、上架都基于这份数据。</p>
            </div>
            <span class="badge-info">{{ props.product.source.sourcePlatform || '未采集' }}</span>
          </div>
          <div class="mt-5 space-y-3">
            <div class="rounded-2xl bg-slate-50 p-4 ring-1 ring-slate-200">
              <p class="text-xs font-semibold text-slate-500">商品标题</p>
              <p class="mt-2 text-base font-bold text-slate-950">{{ props.product.source.title || props.product.name || '待采集' }}</p>
              <p class="mt-2 line-clamp-4 text-sm leading-6 text-slate-600">{{ props.product.source.description || '暂无描述' }}</p>
            </div>
            <div class="grid gap-3 sm:grid-cols-2 xl:grid-cols-1 2xl:grid-cols-2">
              <div class="rounded-2xl bg-slate-50 p-4 ring-1 ring-slate-200"><p class="text-xs font-semibold text-slate-500">价格</p><p class="mt-2 text-base font-bold text-slate-950">{{ props.product.source.price || '-' }} {{ props.product.source.currency }}</p></div>
              <div class="rounded-2xl bg-slate-50 p-4 ring-1 ring-slate-200">
                <p class="text-xs font-semibold text-slate-500">规格</p>
                <p class="mt-2 text-sm font-semibold text-slate-950">{{ props.product.source.dimensions.lengthCm || '-' }} × {{ props.product.source.dimensions.widthCm || '-' }} × {{ props.product.source.dimensions.heightCm || '-' }} cm</p>
                <p class="mt-1 text-sm text-slate-600">{{ props.product.source.weightKg || '-' }} kg</p>
              </div>
            </div>
          </div>
        </section>

        <section class="card">
          <div class="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h3 class="card-title">图片池</h3>
              <p class="muted mt-1">采集原图或上传本地参考图。</p>
            </div>
            <div class="flex gap-2">
              <input ref="fileInput" class="hidden" type="file" accept="image/*" multiple @change="onFilesSelected" />
              <button class="btn btn-outline py-1.5" :disabled="props.loading" @click="openFilePicker">上传</button>
              <button class="btn btn-outline py-1.5" :disabled="props.loading || !props.product.source.imagePool.length" @click="emit('clearImages')">清除</button>
            </div>
          </div>

          <div v-if="props.product.source.imagePool.length" class="mt-5 grid gap-4 sm:grid-cols-2 xl:grid-cols-1">
            <article v-for="image in props.product.source.imagePool.slice(0, 4)" :key="image.id" class="overflow-hidden rounded-2xl border border-slate-200 bg-white">
              <img :src="image.previewUrl || image.url || image.path" :alt="image.id" class="h-36 w-full object-cover" />
              <div class="p-3">
                <div class="flex items-center justify-between gap-2"><p class="truncate text-sm font-semibold text-slate-950">{{ image.id }}</p><span class="badge-muted">{{ image.origin }}</span></div>
                <p class="mt-1 text-xs text-slate-500">{{ image.width }}×{{ image.height }} · {{ image.usage }}</p>
              </div>
            </article>
          </div>
          <div v-else class="mt-5 rounded-2xl border border-dashed border-slate-300 bg-slate-50 p-6 text-center text-sm text-slate-500">暂无图片。导入图片地址、上传图片或通过商品链接采集。</div>
        </section>
      </aside>
    </section>
  </div>
</template>
