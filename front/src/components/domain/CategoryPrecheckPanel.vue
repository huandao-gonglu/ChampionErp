<script setup lang="ts">
import type { CategoryPrecheckResult, CategorySearchResult, CategorySelection, Marketplace, Product, PublishPrecheck, UnknownRecord } from '@/types/workflow'

const props = defineProps<{
  product: Product
  activeMarketplace: Marketplace
  category: CategorySelection | null
  categoryQuery: string
  categoryResults: CategorySearchResult[]
  categoryPrecheck: CategoryPrecheckResult | null
  precheck: PublishPrecheck | null
  payloadPreview: UnknownRecord | null
  loading: boolean
}>()

const emit = defineEmits<{
  updateCategoryQuery: [value: string]
  setMarketplace: [value: Marketplace]
  searchCategory: []
  selectCategory: [item: CategorySearchResult]
  applyCategory: []
  fillAttributes: []
  categoryPrecheck: []
  refreshCategories: []
  precheck: []
  previewPayload: []
  publish: []
}>()

const platforms: Array<{ key: Marketplace; label: string }> = [
  { key: 'mercadolibre', label: 'Mercado Libre' },
  { key: 'wildberries', label: 'Wildberries' },
  { key: 'ozon', label: 'Ozon' },
]
</script>

<template>
  <section class="card">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <div>
        <h2 class="card-title">类目 / 属性 / 发布预检</h2>
        <p class="muted mt-1">类目搜索、必填属性填充、发布前校验和 payload 预览。</p>
      </div>
      <select :value="props.activeMarketplace" class="input w-56" @change="emit('setMarketplace', ($event.target as HTMLSelectElement).value as Marketplace)">
        <option v-for="platform in platforms" :key="platform.key" :value="platform.key">{{ platform.label }}</option>
      </select>
    </div>

    <div class="mt-5 grid gap-6 xl:grid-cols-[1fr_1fr]">
      <article class="rounded-2xl border border-slate-200 bg-slate-50 p-4">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h3 class="font-semibold text-slate-950">类目搜索</h3>
            <p class="mt-1 text-sm text-slate-500">输入中文关键词，例如：瓶 / 项链 / 耳机。</p>
          </div>
          <button class="btn btn-outline py-1.5" :disabled="props.loading" @click="emit('refreshCategories')">刷新官方类目缓存</button>
        </div>
        <div class="mt-4 flex gap-2">
          <input :value="props.categoryQuery" class="input" placeholder="类目关键词" @input="emit('updateCategoryQuery', ($event.target as HTMLInputElement).value)" @keyup.enter="emit('searchCategory')" />
          <button class="btn btn-primary shrink-0" :disabled="props.loading" @click="emit('searchCategory')">搜索</button>
        </div>
        <div class="mt-4 max-h-80 space-y-2 overflow-y-auto">
          <button v-for="item in props.categoryResults" :key="item.id" class="w-full rounded-xl border bg-white p-3 text-left hover:border-brand-300 hover:bg-brand-50" @click="emit('selectCategory', item)">
            <div class="font-semibold text-slate-950">{{ item.name || item.id }}</div>
            <div class="mt-1 text-xs text-slate-500">{{ item.path || item.id }}</div>
          </button>
          <div v-if="!props.categoryResults.length" class="rounded-xl border border-dashed border-slate-300 bg-white p-5 text-center text-sm text-slate-500">暂无搜索结果。</div>
        </div>
      </article>

      <article class="rounded-2xl border border-slate-200 bg-slate-50 p-4">
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
          <button class="btn btn-outline" :disabled="props.loading" @click="emit('applyCategory')">读取必填属性</button>
          <button class="btn btn-primary" :disabled="props.loading" @click="emit('fillAttributes')">AI 填充属性</button>
          <button class="btn btn-outline" :disabled="props.loading" @click="emit('categoryPrecheck')">类目预检</button>
        </div>
        <div class="mt-4 flex flex-wrap gap-2">
          <span v-for="attr in props.category?.requiredAttributes || []" :key="attr.id" class="badge-muted">{{ attr.name || attr.id }}</span>
          <span v-if="!props.category?.requiredAttributes.length" class="badge-muted">待读取属性</span>
        </div>
        <div class="mt-4 grid gap-2">
          <label v-for="(_, key) in props.product.drafts[props.activeMarketplace].attributes" :key="key" class="block">
            <span class="text-xs font-semibold text-slate-500">{{ key }}</span>
            <input v-model="props.product.drafts[props.activeMarketplace].attributes[key]" class="input mt-1" />
          </label>
        </div>
        <div v-if="props.categoryPrecheck" class="mt-4 rounded-2xl p-3 text-sm ring-1" :class="props.categoryPrecheck.ok ? 'bg-emerald-50 text-emerald-800 ring-emerald-200' : 'bg-amber-50 text-amber-800 ring-amber-200'">
          <div class="font-semibold">{{ props.categoryPrecheck.ok ? '类目预检通过' : '类目预检需处理' }}</div>
          <ul v-if="props.categoryPrecheck.missingFields.length || props.categoryPrecheck.errors.length" class="mt-2 list-inside list-disc">
            <li v-for="item in [...props.categoryPrecheck.missingFields, ...props.categoryPrecheck.errors]" :key="item">{{ item }}</li>
          </ul>
        </div>
      </article>
    </div>

    <div class="mt-6 grid gap-6 xl:grid-cols-2">
      <article class="rounded-2xl border p-4" :class="props.precheck?.ok ? 'border-emerald-200 bg-emerald-50' : 'border-slate-200 bg-slate-50'">
        <div class="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h3 class="font-semibold text-slate-950">预检结果</h3>
            <p class="mt-2 text-sm" :class="props.precheck?.ok ? 'text-emerald-700' : 'text-slate-600'">{{ props.precheck ? (props.precheck.ok ? '预检通过，可以发布。' : '预检未通过。') : '尚未执行预检。' }}</p>
          </div>
          <div class="flex flex-wrap gap-2">
            <button class="btn btn-outline" :disabled="props.loading" @click="emit('precheck')">上架预检</button>
            <button class="btn btn-outline" :disabled="props.loading" @click="emit('previewPayload')">Payload 预览</button>
            <button class="btn btn-primary" :disabled="props.loading || !props.precheck?.ok" @click="emit('publish')">确认加入队列</button>
          </div>
        </div>
        <ul v-if="props.precheck?.errors.length" class="mt-3 list-inside list-disc text-sm text-rose-700"><li v-for="err in props.precheck.errors" :key="err">{{ err }}</li></ul>
        <ul v-if="props.precheck?.warnings.length" class="mt-3 list-inside list-disc text-sm text-amber-700"><li v-for="warning in props.precheck.warnings" :key="warning">{{ warning }}</li></ul>
      </article>

      <article class="rounded-2xl border border-slate-200 bg-slate-50 p-4">
        <h3 class="font-semibold text-slate-950">Payload 预览</h3>
        <pre class="mt-3 max-h-80 overflow-auto rounded-xl bg-slate-950 p-3 text-xs text-slate-100">{{ props.payloadPreview ? JSON.stringify(props.payloadPreview, null, 2) : '尚未生成 payload。' }}</pre>
      </article>
    </div>
  </section>
</template>
