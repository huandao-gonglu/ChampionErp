<script setup lang="ts">
import { computed } from 'vue'
import DraftLanguageSelect from '@/components/domain/DraftLanguageSelect.vue'
import DraftMarketSelect from '@/components/domain/DraftMarketSelect.vue'
import type { DraftDetail, DraftProductContext, MarketplaceOption, MarketplaceSiteToSell, MarketplaceTargetSite, UnknownRecord } from '@/types/workflow'
import {
  cbtDestinationSelectionReady,
  isMercadoLibreCbtTarget,
  mercadoLibreAccountSiteId,
  mercadoLibreDestinationKey,
  mercadoLibreIsFullyManaged,
  mercadoLibreMarketplaceBindings,
  unauthorizedCbtDestinationCount,
  validCbtDestinationKeys,
  type MercadoLibreMarketplaceBinding,
} from '@/utils/mercadolibreGlobalSelling'

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
  updateSitesToSell: [draft: DraftDetail, target: MarketplaceTargetSite, sitesToSell: MarketplaceSiteToSell[]]
}>()

function listModel(getter: () => string[], setter: (value: string[]) => void) {
  return computed({
    get: () => getter().join('\n'),
    set: (value: string) => setter(value.split(/\n|,/).map((item) => item.trim()).filter(Boolean)),
  })
}

const bulletsText = listModel(() => props.draft.bullets, (value) => { props.draft.bullets = value })

const canGenerateCopy = computed(() => Boolean(props.draft.productId || props.productContext.productId))
const cbtTarget = computed(() => props.draft.targetSites.find(isMercadoLibreCbtTarget) || null)
const cbtAccountSiteId = computed(() => mercadoLibreAccountSiteId(props.storeConfig))
const cbtDestinationBindings = computed(() => mercadoLibreMarketplaceBindings(props.storeConfig))
const cbtFullyManaged = computed(() => mercadoLibreIsFullyManaged(props.storeConfig))
const selectedCbtDestinationKeys = computed(() => (
  cbtTarget.value ? validCbtDestinationKeys(cbtTarget.value, props.storeConfig) : new Set<string>()
))
const unauthorizedCbtDestinations = computed(() => (
  cbtTarget.value ? unauthorizedCbtDestinationCount(cbtTarget.value, props.storeConfig) : 0
))
const canSaveDraft = computed(() => (
  Boolean(props.draft.draftId) && cbtDestinationSelectionReady(props.draft, props.storeConfig)
))

const cbtDestinationError = computed(() => {
  if (!cbtTarget.value) return ''
  if (cbtAccountSiteId.value !== 'CBT') return '当前店铺尚未验证为 CBT Global Selling 账号，请先重新验证授权。'
  if (cbtFullyManaged.value) return '当前 CBT 账号为 Fully Managed，标准售价与销售目的地流程不适用，不能在此选择或发布。'
  if (!cbtDestinationBindings.value.length) return '授权信息中没有可用销售子市场，请先重新验证授权并同步市场。'
  if (unauthorizedCbtDestinations.value) return '草稿包含已失效或未授权的销售目的地，请按当前授权重新选择。'
  if (!selectedCbtDestinationKeys.value.size) return '至少选择一个销售国家/物流后才能保存、核价与发布。'
  return ''
})

function cbtDestinationSiteLabel(binding: MercadoLibreMarketplaceBinding): string {
  const platform = props.platformOptions.find((option) => option.key === 'mercadolibre')
  const site = platform?.sites.find((option) => option.code.toUpperCase() === binding.siteId)
  return site ? `${site.label}（${binding.siteId}）` : binding.siteId
}

function cbtDestinationMeta(binding: MercadoLibreMarketplaceBinding): string {
  return [
    `物流：${binding.logisticType}`,
    binding.businessModel ? `业务：${binding.businessModel}` : '',
    binding.pricingModel ? `计价：${binding.pricingModel}` : '',
  ].filter(Boolean).join(' · ')
}

function toggleCbtDestination(binding: MercadoLibreMarketplaceBinding, checked: boolean): void {
  const target = cbtTarget.value
  if (!target) return
  const changedKey = mercadoLibreDestinationKey(binding.siteId, binding.logisticType)
  const nextKeys = new Set(selectedCbtDestinationKeys.value)
  if (checked) nextKeys.add(changedKey)
  else nextKeys.delete(changedKey)
  // 只从当前可信授权绑定生成目的地；旧草稿中的失效值不会继续回写。
  const nextSitesToSell = cbtDestinationBindings.value
    .filter((item) => nextKeys.has(mercadoLibreDestinationKey(item.siteId, item.logisticType)))
    .map((item) => ({ siteId: item.siteId, logisticType: item.logisticType }))
  emit('updateSitesToSell', props.draft, target, nextSitesToSell)
}

function removeUnauthorizedCbtDestinations(): void {
  const target = cbtTarget.value
  if (!target) return
  const nextSitesToSell = cbtDestinationBindings.value
    .filter((item) => selectedCbtDestinationKeys.value.has(mercadoLibreDestinationKey(item.siteId, item.logisticType)))
    .map((item) => ({ siteId: item.siteId, logisticType: item.logisticType }))
  emit('updateSitesToSell', props.draft, target, nextSitesToSell)
}
</script>

<template>
  <section class="space-y-5">
    <div v-if="!props.embedded" class="rounded-lg border border-accent-200 bg-accent-50 p-4 dark:border-dark-700 dark:bg-dark-950/70">
      <div class="flex flex-wrap items-start justify-between gap-3">
        <h2 class="text-xl font-black text-slate-950 dark:text-white">草稿编辑</h2>
        <div class="flex flex-wrap gap-2">
          <button class="btn btn-outline" :disabled="props.loading || !canGenerateCopy" @click="emit('generateCopy')">生成/改写本地化文案</button>
          <button class="btn btn-primary" :disabled="props.loading || !canSaveDraft" :title="cbtDestinationError" @click="emit('save')">保存草稿</button>
          <button class="btn btn-outline" @click="emit('close')">关闭</button>
        </div>
      </div>
    </div>

    <section class="space-y-4">
      <div class="grid gap-4 lg:grid-cols-[minmax(0,16rem)_minmax(0,1fr)]">
        <label class="block">
          <span class="text-xs font-semibold text-slate-500 dark:text-accent-300">发布语言</span>
          <DraftLanguageSelect
            class="mt-1"
            :language="props.draft.language"
            :platform-options="props.platformOptions"
            :loading="props.loading"
            @update-language="emit('updateLanguage', props.draft, $event)"
          />
          <span class="mt-1 block text-xs text-accent-500 dark:text-accent-400">切换语言后会重新选择匹配的目标市场；已有文案需要重新生成或检查。</span>
        </label>

        <div class="min-w-0">
          <span class="text-xs font-semibold text-slate-500 dark:text-accent-300">目标市场</span>
          <DraftMarketSelect
            class="mt-1"
            :language="props.draft.language"
            :target-sites="props.draft.targetSites"
            :platform-options="props.platformOptions"
            :loading="props.loading"
            @update-targets="emit('updateTargets', props.draft, $event)"
          />
        </div>
      </div>
      <section
        v-if="cbtTarget"
        data-testid="cbt-destination-selector"
        class="rounded-lg border border-accent-200 bg-accent-50 p-3 dark:border-dark-700 dark:bg-dark-950/60"
      >
        <div class="flex flex-wrap items-start justify-between gap-2">
          <div>
            <div class="text-sm font-semibold text-accent-950 dark:text-white">销售国家 / 物流 <span class="text-rose-600">*</span></div>
            <p class="mt-1 text-xs text-accent-500 dark:text-accent-400">CBT 是全局刊登入口，不是销售目的地；这里只能选择当前授权同步到的子市场。</p>
          </div>
          <span class="rounded bg-white px-2 py-1 text-xs text-accent-600 ring-1 ring-accent-200 dark:bg-dark-900 dark:text-accent-200 dark:ring-dark-700">
            已选 {{ selectedCbtDestinationKeys.size }} / {{ cbtDestinationBindings.length }}
          </span>
        </div>
        <div v-if="cbtDestinationBindings.length" class="mt-3 grid gap-2 md:grid-cols-2">
          <label
            v-for="binding in cbtDestinationBindings"
            :key="mercadoLibreDestinationKey(binding.siteId, binding.logisticType)"
            data-testid="cbt-destination-option"
            class="flex cursor-pointer items-start gap-2 rounded-md border border-accent-200 bg-white p-2.5 dark:border-dark-700 dark:bg-dark-900"
          >
            <input
              data-testid="cbt-destination-checkbox"
              class="mt-0.5 size-4 rounded border-accent-300 text-primary-600"
              type="checkbox"
              :checked="selectedCbtDestinationKeys.has(mercadoLibreDestinationKey(binding.siteId, binding.logisticType))"
              :disabled="props.loading || cbtAccountSiteId !== 'CBT' || cbtFullyManaged"
              @change="toggleCbtDestination(binding, ($event.target as HTMLInputElement).checked)"
            />
            <span class="min-w-0">
              <span class="block font-semibold text-accent-800 dark:text-accent-100">{{ cbtDestinationSiteLabel(binding) }}</span>
              <span class="mt-0.5 block text-[11px] text-accent-500 dark:text-accent-400">{{ cbtDestinationMeta(binding) }}</span>
            </span>
          </label>
        </div>
        <div v-if="cbtDestinationError" class="mt-2 flex flex-wrap items-center gap-2">
          <p data-testid="cbt-destination-error" class="text-xs font-semibold text-rose-600 dark:text-rose-200">{{ cbtDestinationError }}</p>
          <button
            v-if="unauthorizedCbtDestinations && !cbtFullyManaged"
            data-testid="cbt-destination-remove-unauthorized"
            type="button"
            class="btn btn-outline px-2 py-1 text-[11px]"
            :disabled="props.loading"
            @click="removeUnauthorizedCbtDestinations"
          >
            移除失效目的地
          </button>
        </div>
      </section>
      <label class="block">
        <span class="text-xs font-semibold text-slate-500">平台标题</span>
        <input v-model="props.draft.title" class="input mt-1" />
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
