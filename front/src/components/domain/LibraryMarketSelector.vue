<script setup lang="ts">
import { computed, watch } from 'vue'
import type { MarketplaceOption, MarketplaceTargetSite, UnknownRecord } from '@/types/workflow'
import { draftLanguageOptions, draftTargetKey, draftTargetLabel, draftTargetsForLanguage, isMercadoLibrePlatform } from '@/utils/draftTargetOptions'
import { mercadoLibreSelectableBindings } from '@/utils/mercadolibreGlobalSelling'

const props = defineProps<{
  modelValue: MarketplaceTargetSite[]
  platformOptions: MarketplaceOption[]
  storeConfig: UnknownRecord
  loading: boolean
}>()
const emit = defineEmits<{ 'update:modelValue': [targets: MarketplaceTargetSite[]] }>()

const languageNames = new Intl.DisplayNames(['zh-CN'], { type: 'language' })
const bindings = computed(() => mercadoLibreSelectableBindings(props.storeConfig))
const groups = computed(() => draftLanguageOptions(props.platformOptions).map(({ value }) => ({
  language: value,
  label: languageNames.of(value) || value,
  targets: draftTargetsForLanguage(props.platformOptions, value).filter((target) => (
    !isMercadoLibrePlatform(target.platform)
    || bindings.value.some((binding) => binding.siteId.toUpperCase() === target.site.toUpperCase())
  )),
})))
const availableTargets = computed(() => groups.value.flatMap((group) => group.targets))
const selectedKeys = computed(() => new Set(props.modelValue.map(keyOf)))
const selectedLanguageCount = computed(() => groups.value.filter((group) => selectedCount(group.targets) > 0).length)

function keyOf(target: MarketplaceTargetSite) {
  return draftTargetKey(target.platform, target.site)
}

function selectedCount(targets: MarketplaceTargetSite[]) {
  return targets.filter((target) => selectedKeys.value.has(keyOf(target))).length
}

function toggleTargets(targets: MarketplaceTargetSite[], checked: boolean) {
  if (props.loading) return
  const keys = new Set(selectedKeys.value)
  targets.forEach((target) => checked ? keys.add(keyOf(target)) : keys.delete(keyOf(target)))
  emit('update:modelValue', availableTargets.value.filter((target) => keys.has(keyOf(target))))
}

function marketMeta(target: MarketplaceTargetSite) {
  if (!isMercadoLibrePlatform(target.platform)) return `${target.site} / ${target.language}`
  const logistics = [...new Set(bindings.value
    .filter((binding) => binding.siteId.toUpperCase() === target.site.toUpperCase())
    .map((binding) => binding.logisticType))]
  return `${target.site} / ${target.language} / 物流 ${logistics.join('、')}`
}

watch(availableTargets, (targets) => {
  const availableKeys = new Set(targets.map(keyOf))
  const retained = props.modelValue.filter((target) => availableKeys.has(keyOf(target)))
  if (retained.length !== props.modelValue.length) emit('update:modelValue', retained)
})
</script>

<template>
  <section aria-label="目标市场" class="rounded-xl border border-accent-200 bg-accent-50/70 p-4 dark:border-dark-700 dark:bg-dark-950/50">
    <div class="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h3 class="text-sm font-bold text-accent-950 dark:text-white">目标市场</h3>
        <p class="muted mt-1 text-xs">每个商品按所选语言生成草稿，并带入该语言下勾选的市场。顶部批量操作和每行「推到草稿」共用此处的选择。</p>
      </div>
      <button class="text-xs text-accent-500 hover:text-accent-950 disabled:opacity-40 dark:text-accent-400 dark:hover:text-white" :disabled="props.loading || !props.modelValue.length" @click="toggleTargets(availableTargets, false)">清空选择</button>
    </div>

    <div class="mt-4 grid gap-3 lg:grid-cols-3">
      <fieldset v-for="group in groups" :key="group.language" :data-language="group.language" class="min-w-0 rounded-lg border border-accent-200 bg-white dark:border-dark-700 dark:bg-dark-900">
        <legend class="sr-only">{{ group.label }}（{{ group.language }}）市场</legend>
        <label class="flex cursor-pointer items-center gap-2.5 border-b border-accent-100 px-3 py-3 dark:border-dark-700">
          <input
            class="size-4 shrink-0 rounded border-accent-300 text-primary-600"
            type="checkbox"
            :aria-label="`全选 ${group.language} 市场`"
            :checked="group.targets.length > 0 && selectedCount(group.targets) === group.targets.length"
            :indeterminate.prop="selectedCount(group.targets) > 0 && selectedCount(group.targets) < group.targets.length"
            :disabled="props.loading || !group.targets.length"
            @change="toggleTargets(group.targets, ($event.target as HTMLInputElement).checked)"
          />
          <span class="min-w-0 flex-1"><span class="font-bold text-accent-950 dark:text-white">{{ group.language }}</span><span class="ml-2 text-xs text-accent-500 dark:text-accent-400">{{ group.label }}</span></span>
          <span class="text-xs tabular-nums text-accent-500 dark:text-accent-400">{{ selectedCount(group.targets) }} / {{ group.targets.length }}</span>
        </label>
        <div class="space-y-1 p-2">
          <label v-for="target in group.targets" :key="keyOf(target)" class="flex cursor-pointer items-start gap-2.5 rounded-md px-2 py-2 transition-colors" :class="selectedKeys.has(keyOf(target)) ? 'bg-primary-50 dark:bg-primary-500/10' : 'hover:bg-accent-50 dark:hover:bg-dark-800'">
            <input
              class="mt-0.5 size-4 shrink-0 rounded border-accent-300 text-primary-600"
              type="checkbox"
              :aria-label="draftTargetLabel(props.platformOptions, target)"
              :checked="selectedKeys.has(keyOf(target))"
              :disabled="props.loading"
              @change="toggleTargets([target], ($event.target as HTMLInputElement).checked)"
            />
            <span class="min-w-0">
              <span class="block text-xs font-semibold text-accent-800 dark:text-accent-100">{{ draftTargetLabel(props.platformOptions, target) }}</span>
              <span class="mt-1 block text-[11px] text-accent-500 dark:text-accent-400">{{ marketMeta(target) }}</span>
            </span>
          </label>
          <p v-if="!group.targets.length" class="muted px-2 py-3 text-xs">此语言暂无可选市场。</p>
        </div>
      </fieldset>
    </div>
    <p v-if="!groups.length" class="muted mt-3 text-xs">暂无目标市场，请先加载平台配置。</p>
    <p class="mt-3 text-xs text-accent-500 dark:text-accent-400" aria-live="polite">已选 <span class="font-semibold text-primary-700 dark:text-primary-300">{{ selectedLanguageCount }}</span> 个语言、<span class="font-semibold text-primary-700 dark:text-primary-300">{{ props.modelValue.length }}</span> 个市场</p>
  </section>
</template>
