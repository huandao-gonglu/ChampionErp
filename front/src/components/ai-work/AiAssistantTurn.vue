<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { PhCaretRight, PhCheck, PhCopy, PhGitBranch } from '@phosphor-icons/vue'
import { i18n } from '@/i18n'
import type { AiTurnTiming, AiUiPart, PendingToolCall } from '@/types/aiWork'
import { presentAssistantParts } from '@/utils/aiMessagePresentation'
import AiMessagePart from './AiMessagePart.vue'

const props = defineProps<{
  parts: AiUiPart[]
  active?: boolean
  pendingToolCalls?: PendingToolCall[]
  timing?: AiTurnTiming
  allowBranch?: boolean
}>()
const emit = defineEmits<{ branch: [] }>()
const t = i18n.global.t
const display = computed(() => presentAssistantParts(props.parts, props.pendingToolCalls, props.active))
const timeline = computed(() => display.value.timeline.filter(entry => entry.part.type !== 'reasoning'))
const process = computed(() => display.value.process.filter(entry => entry.part.type !== 'reasoning'))
const now = ref(Date.now())
let timer: ReturnType<typeof setInterval> | undefined
watch(() => display.value.live, live => {
  clearInterval(timer)
  now.value = Date.now()
  if (live) timer = setInterval(() => { now.value = Date.now() }, 1000)
}, { immediate: true })
const startedAt = computed(() => Date.parse(props.timing?.started_at ?? ''))
const finishedAt = computed(() => Date.parse(props.timing?.finished_at ?? ''))
const duration = computed(() => {
  const end = display.value.live ? now.value : finishedAt.value
  if (!Number.isFinite(startedAt.value) || !Number.isFinite(end)) return ''
  const seconds = Math.max(0, Math.floor((end - startedAt.value) / 1000))
  const minutes = Math.floor(seconds / 60)
  return minutes
    ? t('ai.turn.minutesSeconds', { minutes, seconds: seconds % 60 })
    : t('ai.turn.seconds', { seconds })
})
const processLabel = computed(() => duration.value
  ? t(display.value.live ? 'ai.turn.elapsed' : 'ai.turn.duration', { duration: duration.value })
  : t(display.value.live ? 'ai.turn.processing' : 'ai.turn.process'))
const finishedTime = computed(() => Number.isFinite(finishedAt.value)
  ? new Date(finishedAt.value).toLocaleTimeString(i18n.global.locale.value, { hour: '2-digit', minute: '2-digit', hour12: false })
  : '')
const finishedTitle = computed(() => Number.isFinite(finishedAt.value)
  ? new Date(finishedAt.value).toLocaleString(i18n.global.locale.value, { hour12: false })
  : '')
const responseText = computed(() => display.value.response
  .filter(entry => entry.part.type === 'text')
  .map(entry => (entry.part as { text: string }).text).join('\n\n'))
const copied = ref(false)
const copyError = ref(false)
let copyTimer: ReturnType<typeof setTimeout> | undefined
async function copyResponse() {
  copyError.value = false
  try {
    if (!navigator.clipboard) throw new Error('剪贴板不可用')
    await navigator.clipboard.writeText(responseText.value)
    copied.value = true
    clearTimeout(copyTimer)
    copyTimer = setTimeout(() => { copied.value = false }, 1500)
  } catch { copyError.value = true }
}
onBeforeUnmount(() => { clearInterval(timer); clearTimeout(copyTimer) })
const showProgress = computed(() => {
  if (!props.active || display.value.hasActiveTool) return false
  const latest = display.value.timeline.at(-1)?.part
  // 推理正文不进入聊天视图，只有可见的流式正文可以替代进度提示。
  return !latest || !(latest.type === 'text' && 'state' in latest && latest.state === 'streaming')
})
</script>

<template>
  <div class="space-y-3" data-testid="ai-assistant-turn">
    <div v-if="display.live" class="space-y-1" data-testid="ai-turn-timeline">
      <p class="mb-3 border-b border-slate-200 pb-3 text-xs text-slate-500 dark:border-dark-700 dark:text-accent-300" data-testid="ai-turn-timing" aria-live="off">{{ processLabel }}</p>
      <AiMessagePart
        v-for="entry in timeline"
        :key="entry.key"
        :part="entry.part"
        :pending-kind="entry.pendingKind"
        :active="display.live"
      >
        <template #approval="scope"><slot name="approval" v-bind="scope" /></template>
      </AiMessagePart>
      <p v-if="showProgress" role="status" class="flex items-center gap-2 py-1 text-xs text-slate-500 dark:text-accent-300" data-testid="ai-turn-progress">
        <span class="size-1.5 shrink-0 rounded-full bg-primary-500 motion-safe:animate-pulse" aria-hidden="true" />
        {{ t('ai.turn.processing') }}
      </p>
    </div>
    <template v-else>
      <details
        v-if="process.length"
        class="group/process min-w-0"
        data-testid="ai-turn-process"
      >
        <summary class="flex cursor-pointer list-none items-center gap-1.5 border-b border-slate-200 pb-3 text-xs text-slate-500 focus-visible:outline-primary-500 dark:border-dark-700 dark:text-accent-300 [&::-webkit-details-marker]:hidden">
          {{ processLabel }}
          <PhCaretRight :size="12" class="shrink-0 transition-transform group-open/process:rotate-90" aria-hidden="true" />
        </summary>
        <div class="mt-2 space-y-1">
          <AiMessagePart
            v-for="entry in process"
            :key="entry.key"
            :part="entry.part"
            :pending-kind="entry.pendingKind"
            :active="active"
          />
        </div>
      </details>
      <p v-else-if="duration" class="border-b border-slate-200 pb-3 text-xs text-slate-500 dark:border-dark-700 dark:text-accent-300" data-testid="ai-turn-timing">{{ processLabel }}</p>

      <div v-if="display.attention.length" class="space-y-2" data-testid="ai-turn-attention">
        <AiMessagePart
          v-for="entry in display.attention"
          :key="entry.key"
          :part="entry.part"
          :pending-kind="entry.pendingKind"
          :active="active"
        />
      </div>

      <div v-if="display.response.length" class="space-y-2" data-testid="ai-turn-response">
        <AiMessagePart v-for="entry in display.response" :key="entry.key" :part="entry.part" />
      </div>
      <div v-if="display.response.length" class="flex items-center gap-3 pt-1 text-slate-400 dark:text-accent-400" data-testid="ai-turn-actions">
        <button
          v-if="responseText" type="button" class="group/copy relative p-1 hover:text-slate-700 focus-visible:outline-primary-500 dark:hover:text-accent-100"
          :aria-label="t(copied ? 'ai.turn.copied' : 'ai.turn.copy')" @click="copyResponse"
        >
          <component :is="copied ? PhCheck : PhCopy" :size="16" aria-hidden="true" />
          <span aria-hidden="true" class="pointer-events-none absolute bottom-full left-0 z-20 mb-2 hidden whitespace-nowrap rounded-md bg-slate-800 px-2.5 py-1.5 text-xs text-white shadow-sm group-hover/copy:block group-focus-visible/copy:block dark:bg-dark-600">
            {{ t(copied ? 'ai.turn.copied' : 'ai.turn.copy') }}
          </span>
        </button>
        <button
          v-if="allowBranch" type="button" class="group/branch relative p-1 hover:text-slate-700 focus-visible:outline-primary-500 dark:hover:text-accent-100"
          :aria-label="t('ai.turn.branch')" @click="emit('branch')"
        >
          <PhGitBranch :size="16" aria-hidden="true" />
          <span aria-hidden="true" class="pointer-events-none absolute bottom-full left-0 z-20 mb-2 hidden whitespace-nowrap rounded-md bg-slate-800 px-2.5 py-1.5 text-xs text-white shadow-sm group-hover/branch:block group-focus-visible/branch:block dark:bg-dark-600">
            {{ t('ai.turn.branch') }}
          </span>
        </button>
        <time v-if="finishedTime" :datetime="timing?.finished_at" :title="finishedTitle" class="text-xs">{{ finishedTime }}</time>
      </div>
      <p v-if="copyError" role="alert" class="text-xs text-rose-600 dark:text-rose-300">{{ t('ai.turn.copyFailed') }}</p>
    </template>
  </div>
</template>
