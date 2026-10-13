<script setup lang="ts">
import { computed } from 'vue'
import { PhBrain, PhCaretRight } from '@phosphor-icons/vue'
import { i18n } from '@/i18n'
import type { AiUiPart } from '@/types/aiWork'
import { describeAiTool } from '@/utils/aiToolLabels'
import { aiToolIcon } from '@/utils/aiToolIcons'
import { aiToolName, aiToolStatus, isAiToolPart, toolNeedsAttention, type ToolPartView } from '@/utils/aiMessagePresentation'

const props = defineProps<{
  part: AiUiPart
  pendingKind?: 'approval' | 'external'
  active?: boolean
}>()

const partType = computed(() => (props.part as { type: string }).type)

const isToolPart = computed(() => isAiToolPart(props.part))
const tool = computed(() => props.part as ToolPartView)
const toolName = computed(() => describeAiTool(aiToolName(props.part)))
const toolIcon = computed(() => aiToolIcon(aiToolName(props.part)))

const businessResult = computed(() => (
  tool.value.output && typeof tool.value.output === 'object'
    ? tool.value.output as { ok?: boolean; status?: string; error?: { message?: string } }
    : undefined
))
const status = computed(() => aiToolStatus(props.part, props.pendingKind, props.active))
const toolStatusLabel = computed(() => i18n.global.t(`ai.status.${status.value}`))
const toolError = computed(() => tool.value.errorText || businessResult.value?.error?.message || '')

const toolStatusClass = computed(() => {
  switch (status.value) {
    case 'failed':
      return 'bg-rose-50 text-rose-700 ring-rose-200 dark:bg-rose-500/10 dark:text-rose-200 dark:ring-rose-500/30'
    case 'unknown':
    case 'partial':
    case 'approval':
    case 'denied':
      return 'bg-amber-50 text-amber-800 ring-amber-200 dark:bg-amber-500/10 dark:text-amber-100 dark:ring-amber-500/30'
    case 'completed':
      return 'bg-emerald-50 text-emerald-700 ring-emerald-200 dark:bg-emerald-500/10 dark:text-emerald-200 dark:ring-emerald-500/30'
    case 'script_error':
      return 'bg-slate-100 text-slate-600 ring-slate-200 dark:bg-dark-700 dark:text-accent-200 dark:ring-dark-600'
    default:
      return 'bg-sky-50 text-sky-700 ring-sky-200 dark:bg-sky-500/10 dark:text-sky-200 dark:ring-sky-500/30'
  }
})

function formatJson(value: unknown): string {
  if (value === undefined) return ''
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

const toolInputJson = computed(() => formatJson(tool.value.input))
const toolOutputJson = computed(() => formatJson(tool.value.output))

const sourceTitle = computed(() => {
  const part = props.part as unknown as { title?: string; url?: string; sourceId?: string }
  return part.title || part.url || part.sourceId || '来源'
})

const sourceUrl = computed(() => {
  const part = props.part as unknown as { url?: string }
  return part.url || ''
})

const isSafeUrl = computed(() => /^https?:\/\//i.test(sourceUrl.value))

const fileName = computed(() => {
  const part = props.part as unknown as { filename?: string; mediaType?: string }
  return part.filename || part.mediaType || '文件'
})

const fileUrl = computed(() => (props.part as unknown as { url?: string }).url || '')

const isSafeFileUrl = computed(() => {
  const url = fileUrl.value.trim()
  if (/^https?:\/\//i.test(url)) return true
  return /^data:(?:application\/pdf|text\/plain|image\/(?:png|jpeg|gif|webp)|audio\/[a-z0-9.+-]+|video\/[a-z0-9.+-]+);base64,/i.test(url)
})

const debugJson = computed(() => formatJson(props.part))

/** text / reasoning part 的正文；模板中不做 `as` 断言，统一在脚本内收敛类型。 */
const textPartText = computed(() => (props.part as unknown as { text?: string }).text || '')

const reasoningStreaming = computed(() => (
  (props.part as unknown as { state?: string }).state === 'streaming'
))
</script>

<template>
  <!-- 文本 -->
  <p
    v-if="partType === 'text'"
    class="whitespace-pre-wrap break-words text-sm leading-6"
    data-testid="ai-part-text"
  >
    {{ textPartText }}
  </p>

  <!-- 思考过程：默认折叠，流式时显示进行中 -->
  <details
    v-else-if="partType === 'reasoning'"
    class="group/reasoning min-w-0"
    data-testid="ai-part-reasoning"
  >
    <summary class="flex cursor-pointer list-none items-center gap-2 py-1 text-xs text-slate-500 focus-visible:outline-primary-500 dark:text-accent-300 [&::-webkit-details-marker]:hidden">
      <PhBrain :size="16" class="shrink-0" aria-hidden="true" />
      {{ reasoningStreaming ? '思考中…' : '思考过程' }}
      <PhCaretRight :size="12" class="shrink-0 transition-transform group-open/reasoning:rotate-90" aria-hidden="true" />
    </summary>
    <p class="py-1 pl-6 whitespace-pre-wrap break-words text-xs leading-5 text-slate-600 dark:text-accent-200">
      {{ textPartText }}
    </p>
  </details>

  <!-- 工具在原位置更新；仅展开时展示参数和回执。 -->
  <div v-else-if="isToolPart" class="min-w-0" data-testid="ai-part-tool">
    <details data-testid="ai-tool-details" class="group/tool">
      <summary class="flex cursor-pointer list-none items-center gap-2 rounded py-1 text-xs focus-visible:outline-primary-500 [&::-webkit-details-marker]:hidden">
        <component :is="toolIcon" :size="16" class="shrink-0 text-slate-500 dark:text-accent-300" aria-hidden="true" />
        <span class="min-w-0 break-words text-slate-600 dark:text-accent-200">{{ toolName }}</span>
        <span
          class="shrink-0 rounded px-1.5 py-0.5 text-[10px]"
          :class="toolStatusClass"
          :role="['preparing', 'waiting', 'running'].includes(status) ? 'status' : undefined"
        >{{ toolStatusLabel }}</span>
        <PhCaretRight :size="12" class="shrink-0 text-slate-400 transition-transform group-open/tool:rotate-90" aria-hidden="true" />
      </summary>
      <div class="ml-3 space-y-2 border-l border-slate-200 py-2 pl-3 dark:border-dark-700">
        <template v-if="toolInputJson">
          <p class="text-xs text-slate-500">{{ i18n.global.t('ai.turn.operationDetails') }}</p>
          <pre class="max-h-64 overflow-auto whitespace-pre-wrap break-words text-[11px] leading-5 text-slate-600 dark:text-accent-200">{{ toolInputJson }}</pre>
        </template>
        <template v-if="toolOutputJson">
          <p class="text-xs text-slate-500">{{ i18n.global.t('ai.turn.operationResult') }}</p>
          <pre class="max-h-64 overflow-auto whitespace-pre-wrap break-words text-[11px] leading-5 text-slate-600 dark:text-accent-200">{{ toolOutputJson }}</pre>
        </template>
        <template v-if="status === 'script_error' && toolError">
          <p class="text-xs text-slate-500">{{ toolStatusLabel }}</p>
          <pre data-testid="ai-script-diagnostic" class="max-h-64 overflow-auto whitespace-pre-wrap break-words text-[11px] leading-5 text-slate-600 dark:text-accent-200">{{ toolError }}</pre>
        </template>
      </div>
    </details>
    <p v-if="toolError && status !== 'script_error'" class="ml-3 mt-1 break-words text-xs text-rose-700 dark:text-rose-300" role="alert">
      {{ toolError }}
    </p>
    <p v-else-if="toolNeedsAttention(status) && status !== 'approval'" class="ml-3 mt-1 text-xs text-amber-800 dark:text-amber-200">
      {{ i18n.global.t(`ai.attention.${status}`) }}
    </p>
    <slot v-if="status === 'approval'" name="approval" :tool-call-id="tool.toolCallId" />
  </div>

  <!-- 来源链接 / 来源文档 -->
  <div
    v-else-if="partType === 'source-url' || partType === 'source-document'"
    class="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-xs dark:border-dark-700 dark:bg-dark-800"
    data-testid="ai-part-source"
  >
    <a
      v-if="isSafeUrl"
      :href="sourceUrl"
      target="_blank"
      rel="noopener noreferrer"
      class="font-bold text-primary-600 hover:underline dark:text-primary-300"
    >{{ sourceTitle }}</a>
    <span v-else class="font-bold text-slate-600 dark:text-accent-200">{{ sourceTitle }}</span>
  </div>

  <!-- 文件 -->
  <div
    v-else-if="partType === 'file'"
    class="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-xs dark:border-dark-700 dark:bg-dark-800"
    data-testid="ai-part-file"
  >
    <a
      v-if="isSafeFileUrl"
      :href="fileUrl"
      target="_blank"
      rel="noopener noreferrer"
      class="font-bold text-primary-600 hover:underline dark:text-primary-300"
    >{{ fileName }}</a>
    <span v-else class="font-bold text-slate-600 dark:text-accent-200">{{ fileName }}</span>
  </div>

  <!-- step-start 不渲染内容 -->
  <span v-else-if="partType === 'step-start'" class="hidden" data-testid="ai-part-step-start" />

  <!-- data-* 与未知 part：折叠到调试展示，不影响整条消息 -->
  <details
    v-else
    class="rounded-lg border border-dashed border-slate-300 px-3 py-2 dark:border-dark-600"
    data-testid="ai-part-debug"
  >
    <summary class="cursor-pointer select-none text-[11px] font-bold text-slate-400">
      详细信息
    </summary>
    <pre class="mt-1 overflow-auto whitespace-pre-wrap break-words font-mono text-[11px] leading-5 text-slate-500 dark:text-accent-300">{{ debugJson }}</pre>
  </details>
</template>
