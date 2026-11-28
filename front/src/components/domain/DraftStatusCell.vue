<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import { statusBadgeClass, workflowStatusLabel } from '@/utils/status'
import type {
  DraftIndexItem,
  DraftProgressStep,
  DraftProgressStepState,
  DraftProgressTab,
  DraftProgressTone,
  DraftPublishState,
} from '@/types/workflow'

const props = defineProps<{
  item: DraftIndexItem
  publishState: DraftPublishState
  disabled?: boolean
}>()

const emit = defineEmits<{
  openStep: [tab: DraftProgressTab]
}>()

const triggerRef = ref<HTMLElement | null>(null)
const hintVisible = ref(false)
const hoveredStepKey = ref('')
const hintStyle = ref<Record<string, string>>({})
let hideTimer: ReturnType<typeof setTimeout> | null = null

const progress = computed(() => props.item.progress)
const hasSteps = computed(() => Boolean(progress.value?.steps?.length))
const isForbidden = computed(() => Boolean(progress.value?.forbidden || props.item.status === 'category_forbidden'))
// 仅在发布中 / 已发布 / 非禁售发布失败时展示单行发布态徽章；其余待发布（含禁售类目）直接展示单行 6 步胶囊。
const showPublishBadgeOnly = computed(() => {
  if (isForbidden.value && props.publishState !== 'publishing' && props.publishState !== 'published') {
    return false
  }
  return props.publishState !== 'unpublished'
})

const fallbackLabel = computed(() => {
  if (progress.value?.statusLabel) return progress.value.statusLabel
  return workflowStatusLabel(props.item.status)
})

const fallbackBadgeClass = computed(() => {
  if (progress.value?.statusTone) return toneBadgeClass(progress.value.statusTone)
  return statusBadgeClass(props.item.status)
})

function toneBadgeClass(tone: DraftProgressTone): string {
  if (tone === 'danger') return 'badge-danger'
  if (tone === 'success') return 'badge-success'
  if (tone === 'warning') {
    return 'inline-flex items-center rounded-full bg-amber-50 px-2.5 py-0.5 text-xs font-semibold text-amber-700 ring-1 ring-amber-200 dark:bg-amber-500/15 dark:text-amber-200 dark:ring-amber-500/30'
  }
  if (tone === 'info') return 'badge-info'
  return 'badge-muted'
}

function stepPillClass(state: DraftProgressStepState): string {
  if (state === 'done') {
    return 'border-emerald-200 bg-emerald-50 text-emerald-700 hover:border-emerald-300 dark:border-emerald-500/35 dark:bg-emerald-500/15 dark:text-emerald-200'
  }
  if (state === 'blocked') {
    return 'border-rose-300 bg-rose-50 text-rose-700 hover:border-rose-400 dark:border-rose-500/45 dark:bg-rose-500/15 dark:text-rose-200'
  }
  if (state === 'attention') {
    return 'border-amber-300 bg-amber-50 text-amber-700 hover:border-amber-400 dark:border-amber-500/40 dark:bg-amber-500/15 dark:text-amber-200'
  }
  if (state === 'partial') {
    return 'border-dashed border-sky-300 bg-sky-50 text-sky-700 hover:border-sky-400 dark:border-sky-500/45 dark:bg-sky-500/15 dark:text-sky-200'
  }
  return 'border-accent-200 bg-accent-50 text-accent-400 hover:border-accent-300 hover:text-accent-600 dark:border-dark-700 dark:bg-dark-800/70 dark:text-accent-400 dark:hover:border-dark-600'
}

function stepDotClass(state: DraftProgressStepState): string {
  if (state === 'done') return 'bg-emerald-500'
  if (state === 'blocked') return 'bg-rose-500'
  if (state === 'attention') return 'bg-amber-500'
  if (state === 'partial') return 'bg-sky-500'
  return 'bg-accent-300 dark:bg-dark-600'
}

function stepText(step: DraftProgressStep): string {
  if (step.state === 'blocked') return `${step.short}!`
  if (step.state === 'attention') return `${step.short}?`
  return step.short
}

function updateHintPosition() {
  const el = triggerRef.value
  if (!el || typeof window === 'undefined') return
  const rect = el.getBoundingClientRect()
  const popoverWidth = 340
  const estimatedHeight = 290
  const viewportWidth = window.innerWidth || 1280
  const viewportHeight = window.innerHeight || 800
  const left = Math.max(12, Math.min(rect.left, viewportWidth - popoverWidth - 12))
  const showAbove = rect.bottom + estimatedHeight + 12 > viewportHeight && rect.top > estimatedHeight + 12
  if (showAbove) {
    hintStyle.value = {
      left: `${Math.round(left)}px`,
      bottom: `${Math.round(viewportHeight - rect.top + 6)}px`,
      width: `${popoverWidth}px`,
    }
  } else {
    hintStyle.value = {
      left: `${Math.round(left)}px`,
      top: `${Math.round(rect.bottom + 6)}px`,
      width: `${popoverWidth}px`,
    }
  }
}

function showHint(stepKey = '') {
  if (!progress.value) return
  if (hideTimer) {
    clearTimeout(hideTimer)
    hideTimer = null
  }
  hoveredStepKey.value = stepKey
  updateHintPosition()
  hintVisible.value = true
}

function scheduleHideHint() {
  if (hideTimer) clearTimeout(hideTimer)
  hideTimer = setTimeout(() => {
    hintVisible.value = false
    hoveredStepKey.value = ''
    hideTimer = null
  }, 80)
}

function hideHintImmediately() {
  if (hideTimer) {
    clearTimeout(hideTimer)
    hideTimer = null
  }
  hintVisible.value = false
  hoveredStepKey.value = ''
}

function handleStepClick(step: DraftProgressStep) {
  if (props.disabled) return
  hideHintImmediately()
  emit('openStep', step.tab)
}

onBeforeUnmount(() => {
  if (hideTimer) clearTimeout(hideTimer)
})
</script>

<template>
  <div
    ref="triggerRef"
    class="inline-flex max-w-full items-center"
    data-testid="draft-status-cell"
    @mouseenter="showHint()"
    @mouseleave="scheduleHideHint()"
    @focusin="showHint()"
    @focusout="scheduleHideHint()"
  >
    <span
      v-if="showPublishBadgeOnly || !hasSteps"
      data-testid="draft-status-badge"
      class="inline-flex max-w-full truncate"
      :class="fallbackBadgeClass"
      :title="progress?.alert || fallbackLabel"
    >
      {{ fallbackLabel }}
    </span>

    <div v-else class="inline-flex items-center gap-1" data-testid="draft-progress-steps">
      <button
        v-for="step in progress!.steps"
        :key="step.key"
        type="button"
        :data-testid="`draft-step-${step.key}`"
        :data-step-state="step.state"
        :disabled="props.disabled"
        :aria-label="`${step.label}：${step.summary}`"
        :title="`${step.label}：${step.summary}`"
        class="inline-flex h-[22px] min-w-[24px] items-center justify-center rounded-md border px-1.5 text-[11px] font-semibold leading-none transition hover:-translate-y-px disabled:cursor-not-allowed disabled:opacity-60"
        :class="stepPillClass(step.state)"
        @mouseenter="showHint(step.key)"
        @focus="showHint(step.key)"
        @click.stop="handleStepClick(step)"
      >
        {{ stepText(step) }}
      </button>
    </div>

    <Teleport to="body">
      <div
        v-if="hintVisible && progress"
        role="tooltip"
        data-testid="draft-status-hint"
        :style="hintStyle"
        class="pointer-events-none fixed z-50 rounded-xl border border-accent-200 bg-white p-3 text-left text-xs text-accent-800 shadow-xl dark:border-dark-700 dark:bg-dark-900 dark:text-accent-100"
      >
        <div class="flex items-center justify-between gap-2 border-b border-accent-100 pb-2 dark:border-dark-800">
          <span class="inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-semibold" :class="toneBadgeClass(progress.statusTone)">
            {{ progress.statusLabel }}
          </span>
          <span class="tabular-nums text-[11px] text-accent-500 dark:text-accent-400">
            已完成 {{ progress.completedSteps }}/{{ progress.totalSteps }} 步
          </span>
        </div>

        <div
          v-if="progress.alert"
          data-testid="draft-status-hint-alert"
          class="mt-2 whitespace-normal break-words rounded-lg border px-2.5 py-1.5 text-[11px] leading-relaxed"
          :class="progress.statusTone === 'warning'
            ? 'border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200'
            : 'border-rose-200 bg-rose-50 text-rose-700 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-200'"
        >
          {{ progress.alert }}
        </div>

        <div v-if="progress.markets.length" class="mt-2 space-y-1">
          <div class="text-[11px] font-semibold text-accent-500 dark:text-accent-400">市场明细</div>
          <div
            v-for="market in progress.markets"
            :key="`${market.platform}:${market.site}`"
            class="flex items-start justify-between gap-2 rounded-lg bg-accent-50 px-2 py-1 text-[11px] dark:bg-dark-950/70"
          >
            <div class="flex shrink-0 items-center gap-1.5">
              <span class="font-semibold text-accent-900 dark:text-white">{{ market.label }}</span>
              <span class="rounded-full px-1.5 py-0.5 text-[10px] font-semibold leading-none" :class="toneBadgeClass(market.tone)">
                {{ market.statusLabel }}
              </span>
            </div>
            <span class="min-w-0 whitespace-normal break-words text-right text-accent-600 dark:text-accent-300">
              {{ market.summary }}
            </span>
          </div>
        </div>

        <div v-if="progress.steps.length" class="mt-2 space-y-1 border-t border-accent-100 pt-2 dark:border-dark-800">
          <div
            v-for="step in progress.steps"
            :key="step.key"
            class="flex items-start justify-between gap-2 rounded-md px-1.5 py-0.5 text-[11px]"
            :class="hoveredStepKey === step.key ? 'bg-primary-50/80 dark:bg-primary-500/15' : ''"
          >
            <span class="inline-flex shrink-0 items-center gap-1.5 font-medium text-accent-700 dark:text-accent-200">
              <span class="size-1.5 rounded-full" :class="stepDotClass(step.state)" />
              {{ step.label }}
            </span>
            <span class="min-w-0 whitespace-normal break-words text-right text-accent-500 dark:text-accent-300">
              {{ step.summary }}
            </span>
          </div>
        </div>

        <div v-if="!showPublishBadgeOnly && hasSteps" class="mt-2 border-t border-accent-100 pt-1.5 text-[10px] text-accent-400 dark:border-dark-800 dark:text-accent-500">
          点击任意步骤可直接进入对应编辑页
        </div>
      </div>
    </Teleport>
  </div>
</template>
