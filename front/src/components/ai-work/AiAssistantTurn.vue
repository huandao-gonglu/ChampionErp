<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { PhCaretRight, PhCheck, PhCopy, PhGitBranch } from '@phosphor-icons/vue'
import type { AiTurnTiming, AiUiPart, BusinessWriteReceipt, CodeProgressSnapshot, PendingToolCall } from '@/types/aiWork'
import {
  aiToolName,
  displayToolStatus,
  fatalModelErrorInfo,
  isAiToolPart,
  presentAssistantParts,
  splitLiveTimeline,
  summarizeCompletedProcess,
  type DisplayPart,
  type ToolPartView,
} from '@/utils/aiMessagePresentation'
import { visibleAssistantText } from '@/utils/aiThinkingPresentation'
import {
  computeWindowCharsPerSecond,
  DEER_ACTIVITY_WINDOW_MS,
  measureTurnActivity,
  resolveDeerMotion,
  type ActivitySample,
} from '@/utils/aiDeerMotion'
import AiMessagePart from './AiMessagePart.vue'
import AiRunningDeer from './AiRunningDeer.vue'

const props = defineProps<{
  parts: AiUiPart[]
  writeReceipts?: BusinessWriteReceipt[]
  codeProgress?: Record<string, CodeProgressSnapshot>
  active?: boolean
  pendingToolCalls?: PendingToolCall[]
  timing?: AiTurnTiming
  allowBranch?: boolean
}>()
const emit = defineEmits<{ branch: [] }>()
const processOpen = ref(false)
const earlierOpen = ref(false)
function toggleProcess(event: Event) {
  processOpen.value = (event.target as HTMLDetailsElement).open
}
function toggleEarlier(event: Event) {
  earlierOpen.value = (event.target as HTMLDetailsElement).open
}
const display = computed(() => presentAssistantParts(props.parts, props.pendingToolCalls, props.active, props.writeReceipts))
const timeline = computed(() => display.value.timeline.filter(entry => entry.part.type !== 'reasoning'))
const process = computed(() => display.value.process.filter(entry => entry.part.type !== 'reasoning'))
const hasExternalWork = computed(() => timeline.value.some(entry => entry.pendingKind === 'external'))
const hasPendingInput = computed(() => timeline.value.some(entry => entry.pendingKind === 'input'))
const hasPendingApproval = computed(() => timeline.value.some(entry => entry.pendingKind === 'approval'))
const isActivelyRunning = computed(() => Boolean(props.active) || hasExternalWork.value)
const waitingForUser = computed(() => display.value.live && !isActivelyRunning.value && (hasPendingInput.value || hasPendingApproval.value))
const now = ref(Date.now())
let timer: ReturnType<typeof setInterval> | undefined
watch(() => display.value.live && isActivelyRunning.value, ticking => {
  clearInterval(timer)
  now.value = Date.now()
  if (ticking) timer = setInterval(() => { now.value = Date.now() }, 1000)
}, { immediate: true })
const activeStepStarts = ref<Record<string, number>>({})
watch([timeline, () => display.value.live], ([entries, live]) => {
  if (!live) {
    activeStepStarts.value = {}
    return
  }
  const next: Record<string, number> = {}
  const current = Date.now()
  for (const entry of entries) {
    if (!isAiToolPart(entry.part)) continue
    const st = displayToolStatus(entry, true)
    if (['preparing', 'waiting', 'running'].includes(st)) {
      next[entry.key] = activeStepStarts.value[entry.key] ?? current
    }
  }
  activeStepStarts.value = next
}, { immediate: true })
function stepElapsedSeconds(key: string): number {
  const started = activeStepStarts.value[key]
  return started ? Math.max(0, Math.floor((now.value - started) / 1000)) : 0
}
function entryCodeProgress(entry: DisplayPart): CodeProgressSnapshot | undefined {
  if (!props.codeProgress || !isAiToolPart(entry.part)) return undefined
  const callId = (entry.part as ToolPartView).toolCallId
  return callId ? props.codeProgress[callId] : undefined
}
const liveStepBadge = computed(() => {
  if (waitingForUser.value) return ''
  const tools = timeline.value.filter(entry => isAiToolPart(entry.part))
  if (tools.length < 2) return ''
  const done = tools.filter(entry => !['preparing', 'waiting', 'running', 'approval', 'input'].includes(displayToolStatus(entry, true))).length
  return `${Math.min(done + 1, tools.length)}/${tools.length}`
})
const liveSplit = computed(() => splitLiveTimeline(timeline.value))
const liveLastToolIndex = computed(() => {
  let last = -1
  timeline.value.forEach((entry, index) => {
    if (isAiToolPart(entry.part)) last = index
  })
  return last
})
function isStandaloneLiveCard(entry: DisplayPart): boolean {
  if (!isAiToolPart(entry.part)) return false
  return aiToolName(entry.part) === 'request_user_input' || Boolean(fatalModelErrorInfo(entry.part))
}
function isLiveProcessEntry(entry: DisplayPart, recentIndex: number): boolean {
  if (isStandaloneLiveCard(entry)) return false
  if (isAiToolPart(entry.part)) return true
  const timelineIndex = liveSplit.value.earlier.length + recentIndex
  if (entry.part.type === 'text') return timelineIndex < liveLastToolIndex.value
  return entry.part.type !== 'file' && entry.part.type !== 'source-url' && entry.part.type !== 'source-document'
}
function liveEntryWrapperClass(entry: DisplayPart, recentIndex: number): string {
  if (!isLiveProcessEntry(entry, recentIndex)) {
    return recentIndex > 0 || liveSplit.value.earlier.length ? 'pt-2' : ''
  }
  const rail = 'border-l-2 border-slate-200/80 pl-3 dark:border-dark-700/80'
  if (entry.part.type === 'text') return `${rail} pt-1.5 pb-0.5`
  return `${rail} py-0.5`
}
const startedAt = computed(() => Date.parse(props.timing?.started_at ?? ''))
const finishedAt = computed(() => Date.parse(props.timing?.finished_at ?? ''))
const duration = computed(() => {
  const end = display.value.live ? now.value : finishedAt.value
  if (!Number.isFinite(startedAt.value) || !Number.isFinite(end)) return ''
  const seconds = Math.max(0, Math.floor((end - startedAt.value) / 1000))
  const minutes = Math.floor(seconds / 60)
  return minutes
    ? `${minutes}分钟 ${seconds % 60}秒`
    : `${seconds}秒`
})
const waitingForUserLabel = computed(() => {
  if (hasPendingInput.value && hasPendingApproval.value) return '等待你回复与确认'
  return hasPendingInput.value ? '等待你回复' : '等待你确认'
})
const completedProcessSummary = computed(() => (
  summarizeCompletedProcess(process.value, props.writeReceipts, duration.value)
))
const finishedTime = computed(() => Number.isFinite(finishedAt.value)
  ? new Date(finishedAt.value).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false })
  : '')
const finishedTitle = computed(() => Number.isFinite(finishedAt.value)
  ? new Date(finishedAt.value).toLocaleString('zh-CN', { hour12: false })
  : '')
const responseText = computed(() => display.value.response
  .filter(entry => entry.part.type === 'text')
  .map(entry => visibleAssistantText((entry.part as { text: string }).text)).filter(Boolean).join('\n\n'))
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
const activity = computed(() => measureTurnActivity(props.parts, props.codeProgress))
const liveStatusText = computed(() => {
  if (waitingForUser.value) return waitingForUserLabel.value
  if (display.value.hasActiveTool) return '正在执行…'
  if (activity.value.streamingVisibleText) return '正在回复…'
  return '正在思考…'
})
const activitySamples = ref<ActivitySample[]>([])
const lastActivityAt = ref(Date.now())
const lastToolTickAt = ref(0)
let previousCharCount = activity.value.charCount
let previousProgressCount = activity.value.progressCount
watch(() => display.value.live, () => {
  const current = Date.now()
  activitySamples.value = []
  lastActivityAt.value = current
  lastToolTickAt.value = 0
  previousCharCount = activity.value.charCount
  previousProgressCount = activity.value.progressCount
})
watch(activity, next => {
  if (!display.value.live) {
    previousCharCount = next.charCount
    previousProgressCount = next.progressCount
    return
  }
  const current = Date.now()
  const deltaChars = Math.max(0, next.charCount - previousCharCount)
  const deltaProgress = next.progressCount - previousProgressCount
  previousCharCount = next.charCount
  previousProgressCount = next.progressCount
  if (deltaChars > 0 || deltaProgress !== 0) {
    lastActivityAt.value = current
    if (deltaProgress > 0) lastToolTickAt.value = current
    const cutoff = current - DEER_ACTIVITY_WINDOW_MS
    const kept = activitySamples.value.filter(sample => sample.at >= cutoff)
    if (deltaChars > 0) kept.push({ at: current, chars: deltaChars })
    activitySamples.value = kept
  }
})
const deerMotion = computed(() => {
  const current = Math.max(now.value, lastActivityAt.value)
  const charsPerSecond = computeWindowCharsPerSecond(activitySamples.value, current)
  const silentMs = Math.max(0, current - lastActivityAt.value)
  const recentToolTick = lastToolTickAt.value > 0 && current - lastToolTickAt.value <= 2500
  return resolveDeerMotion({
    live: display.value.live,
    waitingForUser: waitingForUser.value,
    hasActiveTool: display.value.hasActiveTool,
    hasExternalWork: hasExternalWork.value,
    streamingVisibleText: activity.value.streamingVisibleText,
    charsPerSecond,
    silentMs,
    recentToolTick,
  })
})
</script>

<template>
  <div class="space-y-2.5" data-testid="ai-assistant-turn">
    <div v-if="display.live" data-testid="ai-turn-timeline">
      <details
        v-if="liveSplit.earlier.length"
        class="group/earlier min-w-0 border-l-2 border-slate-200/80 pb-1 pl-3 dark:border-dark-700/80"
        data-testid="ai-turn-earlier-steps"
        @toggle="toggleEarlier"
      >
        <summary class="flex cursor-pointer list-none items-center gap-1.5 py-0.5 text-xs text-slate-400 hover:text-slate-600 focus-visible:outline-primary-500 dark:text-accent-500 dark:hover:text-accent-300 [&::-webkit-details-marker]:hidden">
          已完成 {{ liveSplit.earlierToolCount }} 个前置步骤
          <PhCaretRight :size="11" class="shrink-0 transition-transform group-open/earlier:rotate-90" aria-hidden="true" />
        </summary>
        <div v-if="earlierOpen" class="mt-1 space-y-1">
          <AiMessagePart
            v-for="entry in liveSplit.earlier"
            :key="entry.key"
            :part="entry.part"
            :pending-kind="entry.pendingKind"
            :status-override="entry.statusOverride"
            :progress="entry.progress"
            :code-progress="entryCodeProgress(entry)"
            :write-receipts="writeReceipts"
            :active="display.live"
            variant="process"
          />
        </div>
      </details>
      <div
        v-for="(entry, recentIndex) in liveSplit.recent"
        :key="entry.key"
        :class="liveEntryWrapperClass(entry, recentIndex)"
      >
        <AiMessagePart
          :part="entry.part"
          :pending-kind="entry.pendingKind"
          :status-override="entry.statusOverride"
          :progress="entry.progress"
          :code-progress="entryCodeProgress(entry)"
          :write-receipts="writeReceipts"
          :step-elapsed-seconds="stepElapsedSeconds(entry.key)"
          :active="display.live"
          :variant="isLiveProcessEntry(entry, recentIndex) ? 'process' : 'default'"
        />
      </div>
      <div
        role="status"
        class="flex min-w-0 items-center gap-2 pt-1.5 text-xs text-slate-500 dark:text-accent-400"
        data-testid="ai-turn-progress"
      >
        <AiRunningDeer :gait="deerMotion.gait" :pace="deerMotion.pace" :question="waitingForUser" size="sm" />
        <span
          class="min-w-0 truncate"
          :class="waitingForUser ? 'font-medium text-amber-700 dark:text-amber-300' : 'ai-shimmer-text font-medium'"
          data-testid="ai-turn-status"
        >{{ liveStatusText }}</span>
        <span
          v-if="!waitingForUser && duration"
          class="shrink-0 tabular-nums text-slate-400 dark:text-accent-500"
          data-testid="ai-turn-timing"
          aria-live="off"
        >· {{ duration }}</span>
        <span
          v-if="liveStepBadge"
          class="ml-auto shrink-0 tabular-nums text-[11px] text-slate-400 dark:text-accent-500"
          data-testid="ai-turn-step-count"
        >{{ liveStepBadge }}</span>
      </div>
    </div>
    <template v-else>
      <details
        v-if="process.length"
        class="group/process min-w-0"
        data-testid="ai-turn-process"
        @toggle="toggleProcess"
      >
        <summary class="flex min-w-0 cursor-pointer list-none items-center gap-1.5 py-0.5 text-xs text-slate-500 transition-colors hover:text-slate-700 focus-visible:outline-primary-500 dark:text-accent-400 dark:hover:text-accent-200 [&::-webkit-details-marker]:hidden">
          <span class="min-w-0 truncate">{{ completedProcessSummary }}</span>
          <PhCaretRight :size="12" class="shrink-0 text-slate-400 transition-transform group-open/process:rotate-90 dark:text-accent-500" aria-hidden="true" />
        </summary>
        <div v-if="processOpen" class="mt-1.5 space-y-1 border-l-2 border-slate-200/80 pl-3 dark:border-dark-700/80">
          <AiMessagePart
            v-for="entry in process"
            :key="entry.key"
            :part="entry.part"
            :pending-kind="entry.pendingKind"
            :status-override="entry.statusOverride"
            :write-receipts="writeReceipts"
            :active="active"
            variant="process"
          />
        </div>
      </details>
      <p v-else-if="duration" class="text-xs text-slate-400 dark:text-accent-500" data-testid="ai-turn-timing">用时 {{ duration }}</p>

      <div v-if="display.attention.length" class="space-y-2" data-testid="ai-turn-attention">
        <AiMessagePart
          v-for="entry in display.attention"
          :key="entry.key"
          :part="entry.part"
          :pending-kind="entry.pendingKind"
          :status-override="entry.statusOverride"
          :write-receipts="writeReceipts"
          :active="active"
        />
      </div>

      <div v-if="display.response.length" class="space-y-2" data-testid="ai-turn-response">
        <AiMessagePart v-for="entry in display.response" :key="entry.key" :part="entry.part" />
      </div>
      <div v-if="display.response.length" class="flex items-center gap-3 pt-1 text-slate-400 dark:text-accent-400" data-testid="ai-turn-actions">
        <button
          v-if="responseText" type="button" class="group/copy relative p-1 hover:text-slate-700 focus-visible:outline-primary-500 dark:hover:text-accent-100"
          :aria-label="(copied ? '已复制' : '复制这个回复')" @click="copyResponse"
        >
          <component :is="copied ? PhCheck : PhCopy" :size="16" aria-hidden="true" />
          <span aria-hidden="true" class="pointer-events-none absolute bottom-full left-0 z-20 mb-2 hidden whitespace-nowrap rounded-md bg-slate-800 px-2.5 py-1.5 text-xs text-white shadow-sm group-hover/copy:block group-focus-visible/copy:block dark:bg-dark-600">
            {{ (copied ? '已复制' : '复制这个回复') }}
          </span>
        </button>
        <button
          v-if="allowBranch" type="button" class="group/branch relative p-1 hover:text-slate-700 focus-visible:outline-primary-500 dark:hover:text-accent-100"
          aria-label="分支到新的聊天" @click="emit('branch')"
        >
          <PhGitBranch :size="16" aria-hidden="true" />
          <span aria-hidden="true" class="pointer-events-none absolute bottom-full left-0 z-20 mb-2 hidden whitespace-nowrap rounded-md bg-slate-800 px-2.5 py-1.5 text-xs text-white shadow-sm group-hover/branch:block group-focus-visible/branch:block dark:bg-dark-600">
            分支到新的聊天
          </span>
        </button>
        <time v-if="finishedTime" :datetime="timing?.finished_at" :title="finishedTitle" class="text-xs">{{ finishedTime }}</time>
      </div>
      <p v-if="copyError" role="alert" class="text-xs text-rose-600 dark:text-rose-300">复制失败，请重试或手动选择回复文字。</p>
    </template>
  </div>
</template>
