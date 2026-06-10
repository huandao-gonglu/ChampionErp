<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { CategoryPrecheckResult, CategorySearchResult, CategorySelection, Marketplace, PrecheckIssue, Product, ProductIndexItem, PublishPrecheck, UnknownRecord } from '@/types/workflow'

const props = defineProps<{
  product: Product
  activeMarketplace: Marketplace
  category: CategorySelection | null
  categoryQuery: string
  categoryResults: CategorySearchResult[]
  categoryPrecheck: CategoryPrecheckResult | null
  precheck: PublishPrecheck | null
  payloadPreview: UnknownRecord | null
  productsIndex: ProductIndexItem[]
  categoryCacheStatus: UnknownRecord
  loading: boolean
}>()

const emit = defineEmits<{
  updateCategoryQuery: [value: string]
  setMarketplace: [value: Marketplace]
  searchCategory: []
  suggestCategory: []
  selectCategory: [item: CategorySearchResult]
  applyCategory: []
  fillAttributes: []
  categoryPrecheck: []
  refreshCategories: []
  precheck: []
  previewPayload: []
  publish: []
  claimCurrent: []
  loadProduct: [item: ProductIndexItem]
  refreshProducts: []
}>()

const platforms: Array<{ key: Marketplace; label: string }> = [
  { key: 'mercadolibre', label: 'Mercado Libre' },
  { key: 'wildberries', label: 'Wildberries' },
  { key: 'ozon', label: 'Ozon' },
]

const selectedProductId = ref('')
const showOptionalAttributes = ref(false)

const hasCurrentProduct = computed(() => Boolean(props.product.productId || props.product.name || props.product.source.title))

const currentProductTitle = computed(() => props.product.source.title || props.product.name || props.product.productId || '尚未选择商品')

const selectedProduct = computed(() => props.productsIndex.find((item) => item.productId === selectedProductId.value) || null)
const activeDraft = computed(() => {
  const draft = props.product.drafts[props.activeMarketplace]
  if (!draft.packageDimensions) {
    draft.packageDimensions = { lengthCm: '', widthCm: '', heightCm: '', weightKg: '' }
  }
  if (!Array.isArray(draft.saleTerms)) {
    draft.saleTerms = []
  }
  if (typeof draft.upc !== 'string') {
    draft.upc = ''
  }
  if (typeof draft.allowGtinExemption !== 'boolean') {
    draft.allowGtinExemption = false
  }
  return draft
})
const blockingIssues = computed(() => props.precheck?.errorItems || [])
const warningIssues = computed(() => props.precheck?.warningItems || [])
const canQueuePublish = computed(() => Boolean(hasCurrentProduct.value && (props.precheck?.ok || activeDraft.value.status === 'ready_to_publish')))
const publishReadiness = computed(() => {
  if (!props.precheck && activeDraft.value.status === 'ready_to_publish') return '已保存为校验通过，可以加入发布队列。'
  if (!props.precheck) return '点击上架预检后，这里会变成可处理清单。'
  if (props.precheck.ok) return '预检通过，可以加入发布队列。'
  return `还剩 ${blockingIssues.value.length} 个阻断项，先在本页补齐能直接处理的字段。`
})
const categoryCacheSummary = computed(() => {
  const status = props.categoryCacheStatus || {}
  const jobStatus = String(status.status || '')
  const stage = String(status.stage || '')
  const progress = Number(status.progress || 0)
  if (jobStatus === 'running' || jobStatus === 'queued') {
    const visited = status.visited ?? 0
    const max = status.max_categories ?? status.maxCategories ?? 500
    const records = status.records ?? 0
    const partialImported = status.partial_imported ?? status.partialImported
    const queued = status.queued ?? 0
    const updatedAt = status.updated_at || status.updatedAt
    return `进度 ${progress}% / 已访问 ${visited}/${max} / 已抓取 ${records} / 已写入 ${partialImported ?? 0} / 队列 ${queued}${stage ? ` / ${stage}` : ''}${updatedAt ? ` / ${updatedAt}` : ''}`
  }
  const records = status.records
  const imported = status.imported
  const site = status.site
  const updatedAt = status.updated_at || status.updatedAt
  if (imported !== undefined) return `本次同步 ${imported} 条${site ? ` / ${site}` : ''}`
  if (records !== undefined) return `本地缓存 ${records} 条${updatedAt ? ` / ${updatedAt}` : ''}`
  return ''
})
const categoryRefreshProgress = computed(() => {
  const status = props.categoryCacheStatus || {}
  const progress = Number(status.progress || 0)
  if (!Number.isFinite(progress)) return 0
  return Math.max(0, Math.min(100, progress))
})
const isCategoryRefreshRunning = computed(() => ['queued', 'running'].includes(String(props.categoryCacheStatus?.status || '')) || props.loading)

const attributeFields = computed(() => {
  const draftAttrs = props.product.drafts[props.activeMarketplace].attributes
  const fields = new Map<string, { id: string; name: string; required: boolean }>()
  for (const attr of props.category?.requiredAttributes || []) {
    if (attr.id) fields.set(attr.id, { id: attr.id, name: attr.name || attr.id, required: attr.required })
  }
  for (const attr of props.category?.optionalAttributes || []) {
    if (attr.id && !fields.has(attr.id)) fields.set(attr.id, { id: attr.id, name: attr.name || attr.id, required: false })
  }
  for (const key of Object.keys(draftAttrs)) {
    if (!fields.has(key)) fields.set(key, { id: key, name: key, required: false })
  }
  return [...fields.values()]
})
const requiredAttributeFields = computed(() => attributeFields.value.filter((attr) => attr.required || isMissingAttribute(attr.id)))
const optionalAttributeFields = computed(() => attributeFields.value.filter((attr) => !attr.required && !isMissingAttribute(attr.id)))

function isMissingAttribute(attrId: string) {
  const missing = [
    ...(props.categoryPrecheck?.missingFields || []),
    ...(props.categoryPrecheck?.errors || []),
    ...(props.precheck?.errorItems?.map((item) => item.field) || []),
  ]
  return missing.some((field) => field === attrId || field === `attributes.${attrId}` || field.endsWith(`.${attrId}`))
}

function issueTitle(issue: PrecheckIssue) {
  return [issue.field, issue.message].filter(Boolean).join('：')
}

function loadSelectedProduct() {
  if (selectedProduct.value) emit('loadProduct', selectedProduct.value)
}

function hasIssue(field: string, code = '') {
  return blockingIssues.value.some((issue) => issue.field === field || issue.code === code || issue.field.startsWith(`${field}.`))
}

function generateSku() {
  const source = props.product.productId || props.product.source.sourceUrl || props.product.name || activeDraft.value.title || Date.now().toString()
  const suffix = source.replace(/[^a-zA-Z0-9]+/g, '').slice(-8).toUpperCase() || Date.now().toString().slice(-6)
  const model = (activeDraft.value.attributes.MODEL || props.product.model || 'ML').replace(/[^a-zA-Z0-9]+/g, '').slice(0, 10).toUpperCase() || 'ML'
  const sku = `${model}-${suffix}`
  props.product.sku = sku
  activeDraft.value.sku = sku
}

function useDefaultStock() {
  props.product.stock = props.product.stock || '10'
  activeDraft.value.stock = activeDraft.value.stock || props.product.stock || '10'
}

function useSellerWarranty() {
  activeDraft.value.saleTerms = [
    { id: 'WARRANTY_TYPE', value_name: 'Garantía del vendedor' },
    { id: 'WARRANTY_TIME', value_name: '30 días' },
  ]
}

watch(
  () => props.product.productId,
  (productId) => {
    if (productId) selectedProductId.value = productId
  },
  { immediate: true },
)
</script>

<template>
  <section class="card">
    <article class="mb-6 rounded-2xl border border-slate-200 bg-slate-50 p-4">
      <div class="flex flex-wrap items-start justify-between gap-4">
        <div class="min-w-0">
          <p class="text-xs font-semibold text-slate-500">当前预检商品</p>
          <div class="mt-2 flex flex-wrap items-center gap-3">
            <img v-if="props.product.source.imagePool[0]?.previewUrl || props.product.source.imagePool[0]?.url" :src="props.product.source.imagePool[0]?.previewUrl || props.product.source.imagePool[0]?.url" class="size-14 rounded object-cover" />
            <div class="min-w-0">
              <h3 class="truncate font-semibold text-slate-950">{{ currentProductTitle }}</h3>
              <div class="mt-1 flex flex-wrap gap-2 text-xs text-slate-500">
                <span>{{ props.product.sku || props.product.drafts[props.activeMarketplace].sku || '无 SKU' }}</span>
                <span>{{ props.product.source.sourcePlatform || '来源未记录' }}</span>
                <span>{{ props.product.drafts[props.activeMarketplace].status || 'pending' }}</span>
              </div>
            </div>
          </div>
          <p v-if="!hasCurrentProduct" class="mt-3 text-sm text-amber-700">请先从商品库选择要预检的商品，再执行类目属性和上架预检。</p>
        </div>
        <div class="flex w-full flex-wrap gap-2 lg:w-auto lg:min-w-[32rem]">
          <select v-model="selectedProductId" class="input min-w-0 flex-1">
            <option value="">从商品库选择商品</option>
            <option v-for="item in props.productsIndex" :key="item.productId" :value="item.productId">{{ item.title || item.productId }}</option>
          </select>
          <button class="btn btn-outline" :disabled="props.loading" @click="emit('refreshProducts')">刷新商品库</button>
          <button class="btn btn-primary" :disabled="props.loading || !selectedProduct" @click="loadSelectedProduct">加载商品</button>
          <button class="btn btn-secondary" :disabled="props.loading || !hasCurrentProduct" @click="emit('claimCurrent')">推到平台草稿箱</button>
        </div>
      </div>
    </article>

    <div class="flex flex-wrap items-center justify-between gap-3">
      <div>
        <h2 class="card-title">类目 / 属性 / 发布预检</h2>
        <p class="muted mt-1">类目搜索、必填属性填充、发布前校验和 payload 预览。</p>
      </div>
      <select :value="props.activeMarketplace" class="input w-56" @change="emit('setMarketplace', ($event.target as HTMLSelectElement).value as Marketplace)">
        <option v-for="platform in platforms" :key="platform.key" :value="platform.key">{{ platform.label }}</option>
      </select>
    </div>

    <div class="mt-5 grid min-w-0 gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
      <article class="min-w-0 rounded-2xl border border-slate-200 bg-slate-50 p-4">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h3 class="font-semibold text-slate-950">类目搜索</h3>
            <p class="mt-1 text-sm text-slate-500">输入中文关键词，例如：瓶 / 项链 / 耳机。</p>
          </div>
          <button class="btn btn-outline py-1.5" :disabled="props.loading" @click="emit('refreshCategories')">{{ props.loading ? '正在刷新...' : '刷新官方类目缓存' }}</button>
        </div>
        <p class="mt-3 text-xs text-slate-500">
          {{ props.loading ? '正在从 Mercado Libre 官方接口同步类目和必填属性，数量较多时可能需要 1-3 分钟。' : (categoryCacheSummary || '用于把 Mercado Libre 官方类目和必填属性保存到本地，供下面搜索和预检使用。') }}
        </p>
        <div v-if="isCategoryRefreshRunning || categoryCacheSummary" class="mt-3">
          <div class="h-2 overflow-hidden rounded-full bg-slate-200">
            <div class="h-full rounded-full bg-emerald-500 transition-all" :style="{ width: `${categoryRefreshProgress}%` }" />
          </div>
          <div class="mt-1 text-xs text-slate-500">{{ categoryCacheSummary || '准备同步...' }}</div>
        </div>
        <div class="mt-4 flex gap-2">
          <input :value="props.categoryQuery" class="input" placeholder="类目关键词" @input="emit('updateCategoryQuery', ($event.target as HTMLInputElement).value)" @keyup.enter="emit('searchCategory')" />
          <button class="btn btn-outline shrink-0" :disabled="props.loading || !hasCurrentProduct" @click="emit('suggestCategory')">AI 建议类目</button>
          <button class="btn btn-primary shrink-0" :disabled="props.loading" @click="emit('searchCategory')">搜索</button>
        </div>
        <div class="mt-4 max-h-80 space-y-2 overflow-y-auto">
          <button v-for="item in props.categoryResults" :key="item.id" class="w-full rounded-xl border bg-white p-3 text-left hover:border-brand-300 hover:bg-brand-50" @click="emit('selectCategory', item)">
            <div class="flex flex-wrap items-center justify-between gap-2">
              <div class="font-semibold text-slate-950">{{ item.name || item.id }}</div>
              <span v-if="item.raw.score" class="badge-info">AI {{ item.raw.score }}</span>
            </div>
            <div class="mt-1 text-xs text-slate-500">{{ item.path || item.id }}</div>
            <div v-if="item.raw.site || item.raw.source" class="mt-1 text-xs text-slate-400">{{ item.raw.site || '' }}{{ item.raw.source ? ` / ${item.raw.source}` : '' }}</div>
          </button>
          <div v-if="!props.categoryResults.length" class="rounded-xl border border-dashed border-slate-300 bg-white p-5 text-center text-sm text-slate-500">暂无搜索结果。</div>
        </div>
      </article>

      <article class="min-w-0 rounded-2xl border border-slate-200 bg-slate-50 p-4">
        <h3 class="font-semibold text-slate-950">当前类目 / 必填属性</h3>
        <label class="mt-4 block">
          <span class="text-xs font-semibold text-slate-500">类目 ID</span>
          <input v-model="props.product.drafts[props.activeMarketplace].categoryId" class="input mt-1" placeholder="例如 MLM12345" />
        </label>
        <label class="mt-3 block">
          <span class="text-xs font-semibold text-slate-500">类目路径</span>
          <input v-model="props.product.drafts[props.activeMarketplace].categoryPath" class="input mt-1" />
        </label>
        <div class="mt-4 flex flex-wrap gap-2">
          <button class="btn btn-outline" :disabled="props.loading || !hasCurrentProduct" @click="emit('applyCategory')">读取必填属性</button>
          <button class="btn btn-primary" :disabled="props.loading || !hasCurrentProduct" @click="emit('fillAttributes')">AI 填充属性</button>
          <button class="btn btn-outline" :disabled="props.loading || !hasCurrentProduct" @click="emit('categoryPrecheck')">类目预检</button>
        </div>
        <div class="mt-4 flex flex-wrap gap-2">
          <span v-for="attr in requiredAttributeFields" :key="attr.id" class="badge-muted">{{ attr.name || attr.id }}</span>
          <span v-if="!props.category?.requiredAttributes.length" class="badge-muted">待读取属性</span>
        </div>
        <div class="mt-4 grid gap-2">
          <label v-for="attr in requiredAttributeFields" :key="attr.id" class="block">
            <span class="text-xs font-semibold" :class="isMissingAttribute(attr.id) ? 'text-rose-700' : 'text-slate-500'">{{ attr.required ? '* ' : '' }}{{ attr.name || attr.id }}</span>
            <input
              v-model="props.product.drafts[props.activeMarketplace].attributes[attr.id]"
              class="input mt-1"
              :class="isMissingAttribute(attr.id) ? 'border-rose-300 bg-rose-50' : ''"
              :placeholder="attr.id"
            />
          </label>
        </div>
        <div v-if="optionalAttributeFields.length" class="mt-4 rounded-xl border border-slate-200 bg-white p-3">
          <button class="flex w-full items-center justify-between text-left text-sm font-semibold text-slate-700" type="button" @click="showOptionalAttributes = !showOptionalAttributes">
            <span>可选字段 {{ optionalAttributeFields.length }} 个</span>
            <span>{{ showOptionalAttributes ? '收起' : '展开' }}</span>
          </button>
          <div v-if="showOptionalAttributes" class="mt-3 grid gap-2">
            <label v-for="attr in optionalAttributeFields" :key="attr.id" class="block">
              <span class="text-xs font-semibold text-slate-500">{{ attr.name || attr.id }}</span>
              <input v-model="props.product.drafts[props.activeMarketplace].attributes[attr.id]" class="input mt-1" :placeholder="attr.id" />
            </label>
          </div>
        </div>
        <div v-if="props.categoryPrecheck" class="mt-4 rounded-2xl p-3 text-sm ring-1" :class="props.categoryPrecheck.ok ? 'bg-emerald-50 text-emerald-800 ring-emerald-200' : 'bg-amber-50 text-amber-800 ring-amber-200'">
          <div class="font-semibold">{{ props.categoryPrecheck.ok ? '类目预检通过' : '类目预检需处理' }}</div>
          <ul v-if="props.categoryPrecheck.missingFields.length || props.categoryPrecheck.errors.length" class="mt-2 list-inside list-disc">
            <li v-for="item in [...props.categoryPrecheck.missingFields, ...props.categoryPrecheck.errors]" :key="item">{{ item }}</li>
          </ul>
        </div>
      </article>
    </div>

    <div class="mt-6 grid min-w-0 gap-6 xl:grid-cols-[minmax(0,0.95fr)_minmax(0,1.05fr)]">
      <article class="min-w-0 rounded-2xl border border-slate-200 bg-slate-50 p-4">
        <div class="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h3 class="font-semibold text-slate-950">发布必填资料</h3>
            <p class="mt-1 text-sm text-slate-500">{{ publishReadiness }}</p>
          </div>
          <span v-if="props.precheck" class="badge-muted">{{ blockingIssues.length }} 阻断 / {{ warningIssues.length }} 提醒</span>
        </div>

        <div class="mt-4 grid gap-3 md:grid-cols-2">
          <label class="block">
            <span class="text-xs font-semibold" :class="hasIssue('sku', 'SKU_MISSING') ? 'text-rose-700' : 'text-slate-500'">SKU</span>
            <div class="mt-1 flex gap-2">
              <input v-model="activeDraft.sku" class="input" :class="hasIssue('sku', 'SKU_MISSING') ? 'border-rose-300 bg-rose-50' : ''" />
              <button class="btn btn-outline shrink-0 px-3" type="button" @click="generateSku">生成</button>
            </div>
          </label>
          <label class="block">
            <span class="text-xs font-semibold" :class="hasIssue('stock', 'STOCK_MISSING') ? 'text-rose-700' : 'text-slate-500'">库存</span>
            <div class="mt-1 flex gap-2">
              <input v-model="activeDraft.stock" class="input" :class="hasIssue('stock', 'STOCK_MISSING') ? 'border-rose-300 bg-rose-50' : ''" />
              <button class="btn btn-outline shrink-0 px-3" type="button" @click="useDefaultStock">填 10</button>
            </div>
          </label>
          <label class="block">
            <span class="text-xs font-semibold" :class="hasIssue('upc', 'UPC_MISSING') ? 'text-rose-700' : 'text-slate-500'">UPC / GTIN</span>
            <input v-model="activeDraft.upc" class="input mt-1" :class="hasIssue('upc', 'UPC_MISSING') ? 'border-rose-300 bg-rose-50' : ''" />
          </label>
          <label class="mt-6 flex items-center gap-2 text-sm font-semibold text-slate-700">
            <input v-model="activeDraft.allowGtinExemption" type="checkbox" class="size-4 rounded border-slate-300" />
            允许无 UPC 豁免
          </label>
        </div>

        <div class="mt-4 grid gap-3 md:grid-cols-4">
          <label class="block">
            <span class="text-xs font-semibold text-slate-500">长 cm</span>
            <input v-model="activeDraft.packageDimensions.lengthCm" class="input mt-1" />
          </label>
          <label class="block">
            <span class="text-xs font-semibold text-slate-500">宽 cm</span>
            <input v-model="activeDraft.packageDimensions.widthCm" class="input mt-1" />
          </label>
          <label class="block">
            <span class="text-xs font-semibold text-slate-500">高 cm</span>
            <input v-model="activeDraft.packageDimensions.heightCm" class="input mt-1" />
          </label>
          <label class="block">
            <span class="text-xs font-semibold text-slate-500">重量 kg</span>
            <input v-model="activeDraft.packageDimensions.weightKg" class="input mt-1" />
          </label>
        </div>

        <div class="mt-4 rounded-xl border border-slate-200 bg-white p-3">
          <div class="flex flex-wrap items-center justify-between gap-2">
            <div>
              <div class="text-sm font-semibold text-slate-950">售后条款</div>
              <div class="mt-1 text-xs text-slate-500">{{ activeDraft.saleTerms.length ? `已配置 ${activeDraft.saleTerms.length} 条` : '尚未配置 warranty / sale_terms' }}</div>
            </div>
            <button class="btn btn-outline py-1.5" type="button" @click="useSellerWarranty">使用 30 天卖家保修</button>
          </div>
        </div>
      </article>

      <article class="min-w-0 rounded-2xl border p-4" :class="props.precheck?.ok ? 'border-emerald-200 bg-emerald-50' : 'border-slate-200 bg-slate-50'">
        <div class="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h3 class="font-semibold text-slate-950">预检结果</h3>
            <p class="mt-2 text-sm" :class="props.precheck?.ok ? 'text-emerald-700' : 'text-slate-600'">{{ props.precheck ? (props.precheck.ok ? '预检通过，可以发布。' : '预检未通过。') : '尚未执行预检。' }}</p>
          </div>
          <div class="flex flex-wrap gap-2">
            <button class="btn btn-outline" :disabled="props.loading || !hasCurrentProduct" @click="emit('precheck')">上架预检</button>
            <button class="btn btn-outline" :disabled="props.loading || !hasCurrentProduct" @click="emit('previewPayload')">Payload 预览</button>
            <button class="btn btn-primary" :disabled="props.loading || !canQueuePublish" @click="emit('publish')">确认加入队列</button>
          </div>
        </div>
        <ul v-if="props.precheck?.errorItems.length" class="mt-3 space-y-2 text-sm text-rose-700">
          <li v-for="issue in props.precheck.errorItems" :key="`${issue.code}-${issue.field}-${issue.message}`" class="rounded-xl bg-white/70 p-3 ring-1 ring-rose-100">
            <div class="font-semibold">{{ issueTitle(issue) }}</div>
            <div v-if="issue.nextAction" class="mt-1 text-rose-600">{{ issue.nextAction }}</div>
          </li>
        </ul>
        <ul v-else-if="props.precheck?.errors.length" class="mt-3 list-inside list-disc text-sm text-rose-700"><li v-for="err in props.precheck.errors" :key="err">{{ err }}</li></ul>
        <ul v-if="props.precheck?.warningItems.length" class="mt-3 space-y-2 text-sm text-amber-700">
          <li v-for="issue in props.precheck.warningItems" :key="`${issue.code}-${issue.field}-${issue.message}`" class="rounded-xl bg-white/70 p-3 ring-1 ring-amber-100">
            <div class="font-semibold">{{ issueTitle(issue) }}</div>
            <div v-if="issue.nextAction" class="mt-1 text-amber-600">{{ issue.nextAction }}</div>
          </li>
        </ul>
        <ul v-else-if="props.precheck?.warnings.length" class="mt-3 list-inside list-disc text-sm text-amber-700"><li v-for="warning in props.precheck.warnings" :key="warning">{{ warning }}</li></ul>
      </article>

      <article class="min-w-0 rounded-2xl border border-slate-200 bg-slate-50 p-4">
        <h3 class="font-semibold text-slate-950">Payload 预览</h3>
        <pre class="mt-3 max-h-80 w-full max-w-full overflow-auto rounded-xl bg-slate-950 p-3 text-xs text-slate-100">{{ props.payloadPreview ? JSON.stringify(props.payloadPreview, null, 2) : '尚未生成 payload。' }}</pre>
      </article>
    </div>
  </section>
</template>
