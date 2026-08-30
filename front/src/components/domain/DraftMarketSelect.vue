<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type {
  MarketplaceOption,
  MarketplaceSiteToSell,
  MarketplaceTargetSite,
  UnknownRecord,
} from '@/types/workflow'
import {
  draftTargetKey,
  draftTargetLabel,
  draftTargetsForLanguage,
  isMercadoLibreParentSite,
  isMercadoLibrePlatform,
  selectedDraftTargets,
} from '@/utils/draftTargetOptions'
import {
  mercadoLibreDestinationKey,
  mercadoLibreListingModel,
  mercadoLibreListingModelError,
  mercadoLibreSelectableBindings,
  type MercadoLibreMarketplaceBinding,
} from '@/utils/mercadolibreGlobalSelling'

const props = withDefaults(defineProps<{
  language: string
  targetSites: MarketplaceTargetSite[]
  platformOptions: MarketplaceOption[]
  storeConfig?: UnknownRecord
  loading: boolean
  compact?: boolean
}>(), {
  storeConfig: () => ({}),
  compact: false,
})

const emit = defineEmits<{
  updateTargets: [targets: MarketplaceTargetSite[]]
}>()

const open = ref(false)
const editingTargetKeys = ref<string[]>([])
const editingOperationKeys = ref<Record<string, string>>({})
const targetEditError = ref('')

const configuredMarkets = computed(() => draftTargetsForLanguage(props.platformOptions, props.language))
const mercadoLibreBindings = computed(() => mercadoLibreSelectableBindings(props.storeConfig))
const mercadoLibreBindingsBySite = computed(() => {
  const groups = new Map<string, MercadoLibreMarketplaceBinding[]>()
  mercadoLibreBindings.value.forEach((binding) => {
    const group = groups.get(binding.siteId) || []
    group.push(binding)
    groups.set(binding.siteId, group)
  })
  groups.forEach((bindings) => bindings.sort((left, right) => {
    const leftRemote = left.logisticType === 'remote' ? 0 : 1
    const rightRemote = right.logisticType === 'remote' ? 0 : 1
    return leftRemote - rightRemote
      || left.logisticType.localeCompare(right.logisticType)
      || left.sellerId.localeCompare(right.sellerId)
  }))
  return groups
})
const configuredMercadoLibreMarkets = computed(() => configuredMarkets.value.filter((target) => (
  isMercadoLibrePlatform(target.platform)
)))
const availableTargets = computed(() => configuredMarkets.value.filter((target) => (
  !isMercadoLibrePlatform(target.platform)
  || mercadoLibreBindingsBySite.value.has(String(target.site || '').trim().toUpperCase())
)))
const selectedTargets = computed(() => selectedDraftTargets(availableTargets.value, props.targetSites))
const selectedLabels = computed(() => selectedTargets.value.length
  ? selectedTargets.value.map((target) => draftTargetLabel(props.platformOptions, target, true)).join('、')
  : '未选择市场')
const mercadoLibreTarget = computed(() => props.targetSites.find((target) => (
  isMercadoLibreParentSite(target.platform, target.site)
)) || null)

const marketAvailabilityError = computed(() => {
  if (!configuredMercadoLibreMarkets.value.length || mercadoLibreBindings.value.length) return ''
  return mercadoLibreListingModel(props.storeConfig)
    ? '当前语言没有已授权的销售市场，请重新验证授权并同步市场。'
    : mercadoLibreListingModelError(props.storeConfig)
})

function targetKey(target: MarketplaceTargetSite) {
  return draftTargetKey(target.platform, target.site)
}

function bindingsForTarget(target: MarketplaceTargetSite): MercadoLibreMarketplaceBinding[] {
  if (!isMercadoLibrePlatform(target.platform)) return []
  return mercadoLibreBindingsBySite.value.get(String(target.site || '').trim().toUpperCase()) || []
}

function selectedOperationForTarget(target: MarketplaceTargetSite): string {
  return editingOperationKeys.value[targetKey(target)] || ''
}

function initialOperationForTarget(target: MarketplaceTargetSite): string {
  const bindings = bindingsForTarget(target)
  const selected = mercadoLibreTarget.value?.sitesToSell?.find((destination) => (
    String(destination.siteId || '').trim().toUpperCase() === String(target.site || '').trim().toUpperCase()
  ))
  const selectedKey = selected
    ? mercadoLibreDestinationKey(selected.siteId, selected.logisticType)
    : ''
  return bindings.some((binding) => (
    mercadoLibreDestinationKey(binding.siteId, binding.logisticType) === selectedKey
  ))
    ? selectedKey
    : bindings[0]
      ? mercadoLibreDestinationKey(bindings[0].siteId, bindings[0].logisticType)
      : ''
}

function startEdit() {
  editingTargetKeys.value = selectedTargets.value.map(targetKey)
  editingOperationKeys.value = Object.fromEntries(availableTargets.value.flatMap((target) => {
    const operationKey = initialOperationForTarget(target)
    return operationKey ? [[targetKey(target), operationKey]] : []
  }))
  targetEditError.value = ''
  open.value = true
}

function cancelEdit() {
  open.value = false
  editingTargetKeys.value = []
  editingOperationKeys.value = {}
  targetEditError.value = ''
}

function toggleOpen() {
  if (open.value) cancelEdit()
  else startEdit()
}

function toggleTarget(target: MarketplaceTargetSite, checked: boolean) {
  const key = targetKey(target)
  editingTargetKeys.value = checked
    ? Array.from(new Set([...editingTargetKeys.value, key]))
    : editingTargetKeys.value.filter((value) => value !== key)
  if (checked && isMercadoLibrePlatform(target.platform) && !editingOperationKeys.value[key]) {
    editingOperationKeys.value = { ...editingOperationKeys.value, [key]: initialOperationForTarget(target) }
  }
  targetEditError.value = ''
}

function selectOperation(target: MarketplaceTargetSite, operationKey: string) {
  const key = targetKey(target)
  editingOperationKeys.value = { ...editingOperationKeys.value, [key]: operationKey }
  if (!editingTargetKeys.value.includes(key)) {
    editingTargetKeys.value = [...editingTargetKeys.value, key]
  }
  targetEditError.value = ''
}

function selectAllTargets() {
  editingTargetKeys.value = availableTargets.value.map(targetKey)
  const nextOperations = { ...editingOperationKeys.value }
  availableTargets.value.forEach((target) => {
    if (!isMercadoLibrePlatform(target.platform)) return
    nextOperations[targetKey(target)] ||= initialOperationForTarget(target)
  })
  editingOperationKeys.value = nextOperations
  targetEditError.value = ''
}

function clearTargets() {
  editingTargetKeys.value = []
  targetEditError.value = ''
}

function preservedDestination(binding: MercadoLibreMarketplaceBinding): MarketplaceSiteToSell {
  const operationKey = mercadoLibreDestinationKey(binding.siteId, binding.logisticType)
  const previous = mercadoLibreTarget.value?.sitesToSell?.find((item) => (
    mercadoLibreDestinationKey(item.siteId, item.logisticType) === operationKey
  ))
  return {
    ...(previous || {}),
    ...(previous?.saleTerms ? { saleTerms: previous.saleTerms.map((term) => ({ ...term })) } : {}),
    siteId: binding.siteId,
    logisticType: binding.logisticType,
  }
}

function mercadoLibreDestinations(): MarketplaceSiteToSell[] {
  return availableTargets.value.flatMap((target) => {
    if (!isMercadoLibrePlatform(target.platform) || !editingTargetKeys.value.includes(targetKey(target))) return []
    const operationKey = selectedOperationForTarget(target) || initialOperationForTarget(target)
    const binding = bindingsForTarget(target).find((item) => (
      mercadoLibreDestinationKey(item.siteId, item.logisticType) === operationKey
    ))
    return binding ? [preservedDestination(binding)] : []
  })
}

function saveTargets() {
  const existingByKey = new Map(
    props.targetSites.map((target) => [draftTargetKey(target.platform, target.site), target]),
  )
  const selectedNonMercadoTargets = availableTargets.value
    .filter((target) => (
      !isMercadoLibrePlatform(target.platform)
      && editingTargetKeys.value.includes(targetKey(target))
    ))
    .map((target) => {
      const existing = existingByKey.get(targetKey(target))
      if (!existing) return target
      return {
        ...existing,
        ...target,
        listingCurrency: existing.listingCurrency,
        currencyFingerprint: existing.currencyFingerprint,
      }
    })

  const targets: MarketplaceTargetSite[] = [...selectedNonMercadoTargets]
  if (configuredMercadoLibreMarkets.value.length) {
    const existing = mercadoLibreTarget.value
    targets.push({
      ...(existing || {}),
      platform: 'mercadolibre',
      site: 'CBT',
      language: props.language,
      listingCurrency: existing?.listingCurrency || '',
      currencyFingerprint: existing?.currencyFingerprint,
      sitesToSell: mercadoLibreDestinations(),
    })
  }
  if (!targets.length) {
    targetEditError.value = '当前语言没有可保存的市场配置。'
    return
  }
  emit('updateTargets', targets)
  cancelEdit()
}

function targetMeta(target: MarketplaceTargetSite) {
  if (!isMercadoLibrePlatform(target.platform)) {
    return [target.site, target.language, target.listingCurrency || '币种待核验'].filter(Boolean).join(' / ')
  }
  const bindings = bindingsForTarget(target)
  const logistics = bindings.map((binding) => binding.logisticType).join('、')
  return [target.site, target.language, logistics ? `物流 ${logistics}` : '无授权物流'].join(' / ')
}

watch(() => props.language, cancelEdit)
</script>

<template>
  <div class="relative">
    <button
      data-testid="draft-market-select-button"
      type="button"
      class="input flex w-full min-w-0 items-center justify-between gap-1 text-left"
      :class="props.compact ? 'h-8 px-2 py-1 text-xs' : ''"
      :title="selectedTargets.map((target) => draftTargetLabel(props.platformOptions, target)).join('、') || selectedLabels"
      :disabled="props.loading || (!availableTargets.length && !configuredMarkets.length)"
      :aria-expanded="open"
      @click="toggleOpen"
    >
      <span class="min-w-0 truncate">{{ selectedLabels }}</span>
      <span class="shrink-0 text-[11px] text-accent-400 dark:text-accent-500">{{ selectedTargets.length }}/{{ availableTargets.length }}</span>
    </button>
    <div v-if="open" class="absolute left-0 z-20 mt-1 w-80 rounded-lg border border-accent-200 bg-white p-2 text-xs shadow-sm dark:border-dark-700 dark:bg-dark-900">
      <div class="mb-2 flex items-center justify-between gap-2 text-[11px] font-semibold text-accent-500 dark:text-accent-400">
        <span>市场列表</span>
        <span>{{ props.language || '-' }} · 已选 {{ editingTargetKeys.length }}</span>
      </div>
      <div class="mb-2 flex gap-1.5">
        <button data-testid="draft-market-select-all" type="button" class="btn btn-outline px-2 py-1 text-[11px]" :disabled="props.loading || !availableTargets.length" @click="selectAllTargets">全选当前语言</button>
        <button data-testid="draft-market-select-clear" type="button" class="btn btn-outline px-2 py-1 text-[11px]" :disabled="props.loading || !editingTargetKeys.length" @click="clearTargets">清空</button>
      </div>
      <div class="max-h-64 space-y-1 overflow-y-auto pr-1">
        <div
          v-for="target in availableTargets"
          :key="targetKey(target)"
          class="rounded-md px-2 py-1.5 transition hover:bg-accent-50 dark:hover:bg-dark-800"
        >
          <label class="flex cursor-pointer items-start gap-2">
            <input
              data-testid="draft-target-checkbox"
              class="mt-0.5 size-4 rounded border-accent-300 text-primary-600"
              type="checkbox"
              :checked="editingTargetKeys.includes(targetKey(target))"
              :disabled="props.loading"
              @change="toggleTarget(target, ($event.target as HTMLInputElement).checked)"
            />
            <span class="min-w-0">
              <span class="block truncate font-semibold text-accent-800 dark:text-accent-100">{{ draftTargetLabel(props.platformOptions, target) }}</span>
              <span class="block truncate text-[11px] text-accent-500 dark:text-accent-400">{{ targetMeta(target) }}</span>
            </span>
          </label>
          <div
            v-if="isMercadoLibrePlatform(target.platform) && editingTargetKeys.includes(targetKey(target)) && bindingsForTarget(target).length > 1"
            data-testid="draft-market-operation-list"
            class="ml-6 mt-1 space-y-1 border-l border-accent-200 pl-2 dark:border-dark-700"
          >
            <label
              v-for="binding in bindingsForTarget(target)"
              :key="mercadoLibreDestinationKey(binding.siteId, binding.logisticType)"
              class="flex cursor-pointer items-center gap-1.5 text-[11px] text-accent-600 dark:text-accent-300"
            >
              <input
                data-testid="draft-market-operation-radio"
                type="radio"
                :name="`operation-${targetKey(target)}`"
                :value="mercadoLibreDestinationKey(binding.siteId, binding.logisticType)"
                :checked="selectedOperationForTarget(target) === mercadoLibreDestinationKey(binding.siteId, binding.logisticType)"
                :disabled="props.loading"
                @change="selectOperation(target, mercadoLibreDestinationKey(binding.siteId, binding.logisticType))"
              />
              <span>{{ binding.logisticType }}<template v-if="binding.businessModel"> · {{ binding.businessModel }}</template></span>
            </label>
          </div>
        </div>
        <p v-if="!availableTargets.length" class="rounded-md bg-rose-50 px-2 py-1.5 text-rose-700 dark:bg-rose-500/10 dark:text-rose-200">
          {{ marketAvailabilityError || '当前语言没有配置可选市场。' }}
        </p>
      </div>
      <p v-if="targetEditError" class="mt-2 text-[11px] font-semibold text-rose-600 dark:text-rose-200">{{ targetEditError }}</p>
      <div class="mt-2 flex gap-1.5">
        <button data-testid="draft-market-select-save" class="btn btn-primary px-2 py-1 text-[11px]" :disabled="props.loading" @click="saveTargets">保存</button>
        <button class="btn btn-outline px-2 py-1 text-[11px]" :disabled="props.loading" @click="cancelEdit">取消</button>
      </div>
    </div>
  </div>
</template>
