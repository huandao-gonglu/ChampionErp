<script setup lang="ts">
import { computed } from 'vue'
import DraftLanguageSelect from '@/components/domain/DraftLanguageSelect.vue'
import DraftMarketSelect from '@/components/domain/DraftMarketSelect.vue'
import { mercadoLibreListingModel } from '@/utils/mercadolibreGlobalSelling'
import type {
  DraftDetail,
  DraftProductContext,
  MarketplaceOption,
  MarketplaceTargetSite,
  UnknownRecord,
} from '@/types/workflow'

const props = withDefaults(defineProps<{
  draft: DraftDetail
  productContext: DraftProductContext
  platformOptions: MarketplaceOption[]
  storeConfig?: UnknownRecord
  loading: boolean
  embedded?: boolean
}>(), {
  storeConfig: () => ({}),
  embedded: false,
})

const emit = defineEmits<{
  save: []
  generateCopy: []
  close: []
  updateLanguage: [draft: DraftDetail, language: string]
  updateTargets: [draft: DraftDetail, targets: MarketplaceTargetSite[]]
}>()

function listModel(getter: () => string[], setter: (value: string[]) => void) {
  return computed({
    get: () => getter().join('\n'),
    set: (value: string) => setter(value.split(/\n|,/).map((item) => item.trim()).filter(Boolean)),
  })
}

const bulletsText = listModel(() => props.draft.bullets, (value) => { props.draft.bullets = value })
const canGenerateCopy = computed(() => Boolean(props.draft.productId || props.productContext.productId))
const canSaveDraft = computed(() => Boolean(props.draft.draftId))
const saveBlockedReason = computed(() => (
  canSaveDraft.value ? '' : '当前草稿暂无 ID。'
))
const listingModel = computed(() => mercadoLibreListingModel(props.storeConfig))
const showMercadoLibreGlobalTitle = computed(() => (
  props.draft.platform === 'mercadolibre'
  && listingModel.value === 'traditional_global_items'
))
const localizedTitleLabel = computed(() => {
  if (props.draft.platform !== 'mercadolibre') return '平台标题'
  return listingModel.value === 'user_products'
    ? '产品族名称（family_name）'
    : '本地化平台标题'
})
const titleLimit = computed(() => (
  props.platformOptions.find((option) => option.key === props.draft.platform)?.titleLimit
))
</script>

<template>
  <section class="space-y-5">
    <div v-if="!props.embedded" class="rounded-lg border border-accent-200 bg-accent-50 p-4 dark:border-dark-700 dark:bg-dark-950/70">
      <div class="flex flex-wrap items-start justify-between gap-3">
        <h2 class="text-xl font-black text-slate-950 dark:text-white">草稿编辑</h2>
        <div class="flex flex-wrap gap-2">
          <button class="btn btn-outline" :disabled="props.loading || !canGenerateCopy" @click="emit('generateCopy')">生成/改写本地化文案</button>
          <button class="btn btn-primary" :disabled="props.loading || !canSaveDraft" :title="saveBlockedReason" @click="emit('save')">保存草稿</button>
          <button class="btn btn-outline" @click="emit('close')">关闭</button>
        </div>
      </div>
    </div>

    <section class="space-y-4">
      <div class="grid gap-4 lg:grid-cols-[minmax(0,16rem)_minmax(0,1fr)]">
        <label class="block">
          <span data-testid="draft-language-label" class="text-xs font-semibold text-slate-500 dark:text-accent-300">发布语言</span>
          <DraftLanguageSelect
            class="mt-1"
            :language="props.draft.language"
            :platform-options="props.platformOptions"
            :loading="props.loading"
            @update-language="emit('updateLanguage', props.draft, $event)"
          />
          <span class="mt-1 block text-xs text-accent-500 dark:text-accent-400">语言决定这里显示哪些销售市场；切换后请检查市场选择和当前文案。</span>
        </label>

        <div class="min-w-0">
          <span class="text-xs font-semibold text-slate-500 dark:text-accent-300">目标市场</span>
          <DraftMarketSelect
            class="mt-1"
            :language="props.draft.language"
            :target-sites="props.draft.targetSites"
            :platform-options="props.platformOptions"
            :store-config="props.storeConfig"
            :loading="props.loading"
            @update-targets="emit('updateTargets', props.draft, $event)"
          />
        </div>
      </div>
      <label v-if="showMercadoLibreGlobalTitle" class="block">
        <span class="text-xs font-semibold text-slate-500">CBT 根英文标题</span>
        <input
          v-model="props.draft.globalTitle"
          data-testid="draft-global-title-input"
          class="input mt-1"
          :maxlength="titleLimit"
          placeholder="English title used by the CBT global item"
        />
        <span class="mt-1 block text-xs text-accent-500">仅用于传统 Global Items 根 title；下面的平台标题用于所选销售市场的本地语言。</span>
      </label>
      <label class="block">
        <span data-testid="draft-title-label" class="text-xs font-semibold text-slate-500">{{ localizedTitleLabel }}</span>
        <input v-model="props.draft.title" data-testid="draft-title-input" class="input mt-1" :maxlength="titleLimit" />
      </label>
      <label class="block">
        <span class="text-xs font-semibold text-slate-500">商品描述</span>
        <textarea v-model="props.draft.description" class="input mt-1 min-h-36" />
      </label>
      <label class="block">
        <span class="text-xs font-semibold text-slate-500">商品卖点，每行一个</span>
        <textarea v-model="bulletsText" class="input mt-1 min-h-28" />
      </label>
    </section>
  </section>
</template>
