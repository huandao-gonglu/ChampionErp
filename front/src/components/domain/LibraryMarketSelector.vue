<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { PhCaretDown, PhCaretUp, PhCheckCircle, PhGlobe, PhInfo } from '@phosphor-icons/vue'
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

const collapsed = ref(false)
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

// 按各语言市场数量动态分配列宽比例，保留独立卡片边界同时消除小分组大片留白。
const marketGridStyle = computed(() => {
  if (!groups.value.length || groups.value.length > 4) return undefined
  const tracks = groups.value.map((group) => {
    const count = Math.max(1, group.targets.length)
    const minPx = count === 1 ? 205 : count === 2 ? 240 : Math.min(390, 175 + count * 42)
    const weight = Number(Math.min(5.2, 1.45 + (count - 1) * 0.85).toFixed(2))
    return `minmax(${minPx}px, ${weight}fr)`
  })
  return { '--market-cols': tracks.join(' ') }
})

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
  if (!isMercadoLibrePlatform(target.platform)) return ''
  const logistics = [...new Set(bindings.value
    .filter((binding) => binding.siteId.toUpperCase() === target.site.toUpperCase())
    .map((binding) => binding.logisticType))]
  return logistics.length ? `物流 ${logistics.join('、')}` : ''
}

watch(availableTargets, (targets) => {
  const availableKeys = new Set(targets.map(keyOf))
  const retained = props.modelValue.filter((target) => availableKeys.has(keyOf(target)))
  if (retained.length !== props.modelValue.length) emit('update:modelValue', retained)
})
</script>

<template>
  <section aria-label="目标市场" class="rounded-xl border border-accent-200/90 bg-accent-50/70 px-3.5 py-2.5 dark:border-dark-700 dark:bg-dark-900/50">
    <!-- 紧凑顶栏：标题 + 实时已选状态 + 清空/折叠 -->
    <div class="flex flex-wrap items-center justify-between gap-2">
      <div class="flex min-w-0 flex-wrap items-center gap-2">
        <span class="inline-flex items-center gap-1.5 text-xs font-bold text-accent-900 dark:text-white">
          <PhGlobe :size="15" class="text-primary-600 dark:text-primary-400" aria-hidden="true" />
          目标市场
        </span>
        <span
          aria-live="polite"
          class="inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-[11px] font-medium tabular-nums"
          :class="props.modelValue.length
            ? 'bg-primary-100/90 text-primary-800 dark:bg-primary-500/15 dark:text-primary-200'
            : 'bg-amber-100/80 text-amber-800 dark:bg-amber-500/15 dark:text-amber-300'"
        >
          <PhCheckCircle v-if="props.modelValue.length" :size="13" weight="fill" aria-hidden="true" />
          <PhInfo v-else :size="13" aria-hidden="true" />
          <span>已选 {{ selectedLanguageCount }} 个语言、{{ props.modelValue.length }} 个市场</span>
        </span>
        <span v-if="!props.modelValue.length" class="hidden text-[11px] text-accent-500 dark:text-accent-400 sm:inline">
          先勾选目标市场，再点击「推到草稿」按语言生成草稿
        </span>
      </div>

      <div class="flex shrink-0 items-center gap-2">
        <button
          type="button"
          class="rounded-lg px-2 py-1 text-xs font-medium text-accent-500 transition hover:bg-accent-200/60 hover:text-accent-950 disabled:cursor-not-allowed disabled:opacity-40 dark:text-accent-400 dark:hover:bg-white/10 dark:hover:text-white"
          :disabled="props.loading || !props.modelValue.length"
          @click="toggleTargets(availableTargets, false)"
        >
          清空选择
        </button>
        <button
          type="button"
          class="inline-flex items-center gap-1 rounded-lg border border-accent-200 bg-white px-2.5 py-1 text-xs font-medium text-accent-600 transition hover:bg-accent-50 hover:text-accent-950 dark:border-dark-600 dark:bg-dark-800 dark:text-accent-300 dark:hover:bg-dark-700 dark:hover:text-white"
          :aria-expanded="!collapsed"
          @click="collapsed = !collapsed"
        >
          {{ collapsed ? '展开市场' : '收起' }}
          <PhCaretDown v-if="collapsed" :size="12" aria-hidden="true" />
          <PhCaretUp v-else :size="12" aria-hidden="true" />
        </button>
      </div>
    </div>

    <!-- 收起时的紧凑已选芯片预览 -->
    <div v-if="collapsed && props.modelValue.length" class="mt-2 flex flex-wrap gap-1.5">
      <span
        v-for="target in props.modelValue"
        :key="keyOf(target)"
        class="inline-flex items-center rounded-md border border-primary-200/80 bg-primary-50/80 px-2 py-0.5 text-[11px] font-medium text-primary-800 dark:border-primary-500/30 dark:bg-primary-500/15 dark:text-primary-200"
      >
        {{ draftTargetLabel(props.platformOptions, target) }}
      </span>
    </div>

    <!-- 展开时：保留各语言独立卡片区，按市场数量智能分配列宽比例 -->
    <div v-show="!collapsed" class="mt-2.5">
      <div v-if="groups.length" class="market-grid" :style="marketGridStyle">
        <fieldset
          v-for="group in groups"
          :key="group.language"
          :data-language="group.language"
          class="flex min-w-0 flex-col overflow-hidden rounded-xl border border-accent-200/90 bg-white dark:border-dark-700 dark:bg-dark-800/80"
        >
          <legend class="sr-only">{{ group.label }}（{{ group.language }}）市场</legend>
          <label class="flex cursor-pointer items-center gap-2 border-b border-accent-100 bg-accent-50/60 px-3 py-2 dark:border-dark-700/80 dark:bg-dark-900/40">
            <input
              class="size-3.5 shrink-0 cursor-pointer accent-primary-600"
              type="checkbox"
              :aria-label="`全选 ${group.language} 市场`"
              :checked="group.targets.length > 0 && selectedCount(group.targets) === group.targets.length"
              :indeterminate.prop="selectedCount(group.targets) > 0 && selectedCount(group.targets) < group.targets.length"
              :disabled="props.loading || !group.targets.length"
              @change="toggleTargets(group.targets, ($event.target as HTMLInputElement).checked)"
            />
            <span class="min-w-0 flex-1 truncate text-xs">
              <span class="font-bold text-accent-950 dark:text-white">{{ group.label }}</span>
              <span class="ml-1.5 font-mono text-[11px] text-accent-400">{{ group.language }}</span>
            </span>
            <span
              class="shrink-0 rounded-full px-1.5 py-0.5 text-[11px] font-semibold tabular-nums"
              :class="selectedCount(group.targets) ? 'bg-primary-100 text-primary-800 dark:bg-primary-500/20 dark:text-primary-200' : 'text-accent-400 dark:text-accent-500'"
            >
              {{ selectedCount(group.targets) }}/{{ group.targets.length }}
            </span>
          </label>
          <div class="flex flex-1 flex-wrap content-start items-center gap-1.5 p-2">
            <label
              v-for="target in group.targets"
              :key="keyOf(target)"
              class="inline-flex max-w-full cursor-pointer items-center gap-1.5 rounded-lg border px-2.5 py-1.5 transition"
              :class="selectedKeys.has(keyOf(target))
                ? 'border-primary-300 bg-primary-50/90 text-primary-900 dark:border-primary-500/40 dark:bg-primary-500/15 dark:text-primary-100'
                : 'border-accent-200/80 bg-accent-50/50 text-accent-700 hover:border-accent-300 hover:bg-accent-50 dark:border-dark-700 dark:bg-dark-900/50 dark:text-accent-200 dark:hover:border-dark-600'"
            >
              <input
                class="size-3.5 shrink-0 cursor-pointer accent-primary-600"
                type="checkbox"
                :aria-label="draftTargetLabel(props.platformOptions, target)"
                :checked="selectedKeys.has(keyOf(target))"
                :disabled="props.loading"
                @change="toggleTargets([target], ($event.target as HTMLInputElement).checked)"
              />
              <span class="min-w-0">
                <span class="block whitespace-nowrap text-xs font-semibold leading-tight">{{ draftTargetLabel(props.platformOptions, target) }}</span>
                <span v-if="marketMeta(target)" class="mt-0.5 block whitespace-nowrap text-[10px] leading-tight text-accent-500 dark:text-accent-400">{{ marketMeta(target) }}</span>
              </span>
            </label>
            <p v-if="!group.targets.length" class="muted px-1 py-1 text-xs">此语言暂无可选市场。</p>
          </div>
        </fieldset>
      </div>
      <p v-else class="muted py-1 text-xs">暂无目标市场，请先加载平台配置。</p>
    </div>
  </section>
</template>

<style scoped>
.market-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 0.625rem;
}
@media (min-width: 640px) {
  .market-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
@media (min-width: 1120px) {
  .market-grid {
    grid-template-columns: var(--market-cols, repeat(auto-fit, minmax(240px, 1fr)));
  }
}
</style>
