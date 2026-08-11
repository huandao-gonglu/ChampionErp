<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { MarketplaceOption, MarketplaceTargetSite } from '@/types/workflow'
import {
  draftTargetKey,
  draftTargetLabel,
  draftTargetsForLanguage,
  selectedDraftTargets,
} from '@/utils/draftTargetOptions'

const props = withDefaults(defineProps<{
  language: string
  targetSites: MarketplaceTargetSite[]
  platformOptions: MarketplaceOption[]
  loading: boolean
  compact?: boolean
}>(), {
  compact: false,
})

const emit = defineEmits<{
  updateTargets: [targets: MarketplaceTargetSite[]]
}>()

const open = ref(false)
const editingTargetKeys = ref<string[]>([])
const targetEditError = ref('')
const availableTargets = computed(() => draftTargetsForLanguage(props.platformOptions, props.language))
const selectedTargets = computed(() => selectedDraftTargets(availableTargets.value, props.targetSites))
const selectedLabels = computed(() => selectedTargets.value.length
  ? selectedTargets.value.map((target) => draftTargetLabel(props.platformOptions, target, true)).join('、')
  : '未选择市场')

function startEdit() {
  const fallbackTargets = selectedTargets.value.length ? selectedTargets.value : availableTargets.value.slice(0, 1)
  editingTargetKeys.value = fallbackTargets.map((target) => draftTargetKey(target.platform, target.site))
  targetEditError.value = ''
  open.value = true
}

function cancelEdit() {
  open.value = false
  editingTargetKeys.value = []
  targetEditError.value = ''
}

function toggleOpen() {
  if (open.value) cancelEdit()
  else startEdit()
}

function toggleTarget(target: MarketplaceTargetSite, checked: boolean) {
  const key = draftTargetKey(target.platform, target.site)
  editingTargetKeys.value = checked
    ? Array.from(new Set([...editingTargetKeys.value, key]))
    : editingTargetKeys.value.filter((value) => value !== key)
  targetEditError.value = ''
}

function saveTargets() {
  const targets = availableTargets.value.filter((target) => editingTargetKeys.value.includes(draftTargetKey(target.platform, target.site)))
  if (!targets.length) {
    targetEditError.value = '至少选择一个与当前语言匹配的市场。'
    return
  }
  emit('updateTargets', targets)
  cancelEdit()
}

function targetMeta(target: MarketplaceTargetSite) {
  return [target.site, target.language, target.listingCurrency || '币种待核验'].filter(Boolean).join(' / ')
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
      :disabled="props.loading || !availableTargets.length"
      :aria-expanded="open"
      @click="toggleOpen"
    >
      <span class="min-w-0 truncate">{{ selectedLabels }}</span>
      <span class="shrink-0 text-[11px] text-accent-400 dark:text-accent-500">{{ selectedTargets.length }}/{{ availableTargets.length }}</span>
    </button>
    <div v-if="open" class="absolute left-0 z-20 mt-1 w-64 rounded-lg border border-accent-200 bg-white p-2 text-xs shadow-sm dark:border-dark-700 dark:bg-dark-900">
      <div class="mb-2 flex items-center justify-between gap-2 text-[11px] font-semibold text-accent-500 dark:text-accent-400">
        <span>市场列表</span>
        <span>{{ props.language || '-' }} · 已选 {{ editingTargetKeys.length }}</span>
      </div>
      <div class="max-h-48 space-y-1 overflow-y-auto pr-1">
        <label
          v-for="target in availableTargets"
          :key="draftTargetKey(target.platform, target.site)"
          class="flex cursor-pointer items-start gap-2 rounded-md px-2 py-1.5 transition hover:bg-accent-50 dark:hover:bg-dark-800"
        >
          <input
            data-testid="draft-target-checkbox"
            class="mt-0.5 size-4 rounded border-accent-300 text-primary-600"
            type="checkbox"
            :checked="editingTargetKeys.includes(draftTargetKey(target.platform, target.site))"
            :disabled="props.loading"
            @change="toggleTarget(target, ($event.target as HTMLInputElement).checked)"
          />
          <span class="min-w-0">
            <span class="block truncate font-semibold text-accent-800 dark:text-accent-100">{{ draftTargetLabel(props.platformOptions, target) }}</span>
            <span class="block truncate text-[11px] text-accent-500 dark:text-accent-400">{{ targetMeta(target) }}</span>
          </span>
        </label>
        <p v-if="!availableTargets.length" class="rounded-md bg-rose-50 px-2 py-1.5 text-rose-700 dark:bg-rose-500/10 dark:text-rose-200">当前语言没有配置可选站点。</p>
      </div>
      <p v-if="targetEditError" class="mt-2 text-[11px] font-semibold text-rose-600 dark:text-rose-200">{{ targetEditError }}</p>
      <div class="mt-2 flex gap-1.5">
        <button data-testid="draft-market-select-save" class="btn btn-primary px-2 py-1 text-[11px]" :disabled="props.loading || !editingTargetKeys.length" @click="saveTargets">保存</button>
        <button class="btn btn-outline px-2 py-1 text-[11px]" :disabled="props.loading" @click="cancelEdit">取消</button>
      </div>
    </div>
  </div>
</template>
