<script setup lang="ts">
import { computed } from 'vue'
import type { DraftDetail, DraftProductContext, MarketplaceOption } from '@/types/workflow'

const props = defineProps<{
  draft: DraftDetail
  productContext: DraftProductContext
  platformOptions: MarketplaceOption[]
  loading: boolean
}>()

const emit = defineEmits<{
  save: []
  generateCopy: []
  close: []
}>()

function listModel(getter: () => string[], setter: (value: string[]) => void) {
  return computed({
    get: () => getter().join('\n'),
    set: (value: string) => setter(value.split(/\n|,/).map((item) => item.trim()).filter(Boolean)),
  })
}

const bulletsText = listModel(() => props.draft.bullets, (value) => { props.draft.bullets = value })

const contextTitle = computed(() => props.productContext.sourceTitle || props.productContext.title || props.productContext.productId || '-')
const canGenerateCopy = computed(() => Boolean(props.draft.productId || props.productContext.productId))
const sourceProductId = computed(() => props.draft.sourceProductId || props.productContext.sourceProductId || props.productContext.productId || props.draft.productId)
const platformLabel = computed(() => {
  const platform = props.platformOptions.find((item) => item.key === props.draft.platform)
  const site = platform?.sites.find((item) => item.code.toLowerCase() === props.draft.site.toLowerCase())
  return site ? `${platform?.label} · ${site.label}（${site.code}）` : `${platform?.label || props.draft.platform} · ${props.draft.site || '-'}`
})
</script>

<template>
  <section class="space-y-5">
    <div class="rounded-lg border border-accent-200 bg-accent-50 p-4 dark:border-dark-700 dark:bg-dark-950/70">
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div class="min-w-0">
          <p class="text-xs font-semibold uppercase text-primary-600 dark:text-primary-300">{{ platformLabel }}</p>
          <h2 class="mt-2 text-xl font-black text-slate-950 dark:text-white">草稿编辑</h2>
          <p class="mt-1 truncate text-sm text-accent-500 dark:text-accent-300" :title="props.draft.draftId">{{ props.draft.draftId || '未保存草稿' }}</p>
        </div>
        <div class="flex flex-wrap gap-2">
          <button class="btn btn-outline" :disabled="props.loading || !canGenerateCopy" @click="emit('generateCopy')">AI 编辑文案</button>
          <button class="btn btn-primary" :disabled="props.loading || !props.draft.draftId" @click="emit('save')">保存草稿</button>
          <button class="btn btn-outline" @click="emit('close')">关闭</button>
        </div>
      </div>
    </div>

    <div class="grid gap-5 xl:grid-cols-[minmax(0,1fr)_22rem]">
      <section class="space-y-4">
        <div class="grid gap-3 md:grid-cols-2">
          <label class="block md:col-span-2">
            <span class="text-xs font-semibold text-slate-500">平台标题</span>
            <input v-model="props.draft.title" class="input mt-1" />
          </label>
          <label class="block">
            <span class="text-xs font-semibold text-slate-500">价格</span>
            <input v-model="props.draft.price" class="input mt-1" />
          </label>
          <label class="block">
            <span class="text-xs font-semibold text-slate-500">语言</span>
            <input v-model="props.draft.language" class="input mt-1" />
          </label>
          <label class="block">
            <span class="text-xs font-semibold text-slate-500">SKU</span>
            <input v-model="props.draft.sku" class="input mt-1" />
          </label>
          <label class="block">
            <span class="text-xs font-semibold text-slate-500">库存</span>
            <input v-model="props.draft.stock" class="input mt-1" />
          </label>
          <label class="block">
            <span class="text-xs font-semibold text-slate-500">UPC / GTIN</span>
            <input v-model="props.draft.upc" class="input mt-1" />
          </label>
          <label class="block">
            <span class="text-xs font-semibold text-slate-500">类目 ID</span>
            <input v-model="props.draft.categoryId" class="input mt-1" />
          </label>
          <label class="block md:col-span-2">
            <span class="text-xs font-semibold text-slate-500">类目路径</span>
            <input v-model="props.draft.categoryPath" class="input mt-1" />
          </label>
        </div>

        <div class="grid gap-4 xl:grid-cols-2">
          <label class="block xl:col-span-2">
            <span class="text-xs font-semibold text-slate-500">平台描述</span>
            <textarea v-model="props.draft.description" class="input mt-1 min-h-36" />
          </label>
          <label class="block">
            <span class="text-xs font-semibold text-slate-500">平台卖点，每行一个</span>
            <textarea v-model="bulletsText" class="input mt-1 min-h-28" />
          </label>
        </div>
      </section>

      <aside class="space-y-4">
        <section class="rounded-lg border border-accent-200 bg-white p-4 dark:border-dark-700 dark:bg-dark-900/80">
          <h3 class="font-semibold text-slate-950 dark:text-white">来源参考</h3>
          <dl class="mt-3 space-y-3 text-sm">
            <div>
              <dt class="text-xs font-semibold text-accent-500 dark:text-accent-300">来源标题</dt>
              <dd class="mt-1 text-slate-800 dark:text-accent-100">{{ contextTitle }}</dd>
            </div>
            <div>
              <dt class="text-xs font-semibold text-accent-500 dark:text-accent-300">来源平台</dt>
              <dd class="mt-1 text-slate-800 dark:text-accent-100">{{ props.productContext.sourcePlatform || '-' }}</dd>
            </div>
            <div>
              <dt class="text-xs font-semibold text-accent-500 dark:text-accent-300">采购价 / 重量</dt>
              <dd class="mt-1 text-slate-800 dark:text-accent-100">{{ props.productContext.sourcePrice || props.productContext.cost || '-' }} {{ props.productContext.currency }} / {{ props.productContext.weightKg || '-' }} kg</dd>
            </div>
            <div>
              <dt class="text-xs font-semibold text-accent-500 dark:text-accent-300">来源商品 ID</dt>
              <dd class="mt-1 break-all font-mono text-xs text-slate-700 dark:text-accent-200">{{ sourceProductId || '-' }}</dd>
            </div>
            <div>
              <dt class="text-xs font-semibold text-accent-500 dark:text-accent-300">来源链接</dt>
              <dd class="mt-1 break-all text-xs text-slate-700 dark:text-accent-200">{{ props.productContext.sourceUrl || '-' }}</dd>
            </div>
          </dl>
        </section>

        <section class="rounded-lg border border-accent-200 bg-white p-4 dark:border-dark-700 dark:bg-dark-900/80">
          <h3 class="font-semibold text-slate-950 dark:text-white">来源图片</h3>
          <div class="mt-3 grid grid-cols-3 gap-2">
            <img
              v-for="image in props.productContext.imagePool.slice(0, 6)"
              :key="image.id"
              :src="image.previewUrl || image.url || image.path"
              :alt="image.id"
              class="aspect-square rounded object-cover"
            />
            <div v-if="!props.productContext.imagePool.length" class="col-span-3 rounded border border-dashed border-accent-200 p-6 text-center text-sm text-accent-500 dark:border-dark-700 dark:text-accent-300">暂无图片</div>
          </div>
        </section>
      </aside>
    </div>
  </section>
</template>
