<script setup lang="ts">
import { computed } from 'vue'
import type { AiUiPart } from '@/types/aiWork'

interface ToolPartLike {
  type: string
  toolName?: string
  toolCallId?: string
  state?: string
  input?: unknown
  output?: unknown
  errorText?: string
}

const props = defineProps<{
  part: AiUiPart
}>()

const partType = computed(() => (props.part as { type: string }).type)

const isToolPart = computed(
  () => partType.value === 'dynamic-tool' || partType.value.startsWith('tool-'),
)

const tool = computed<ToolPartLike>(() => props.part as unknown as ToolPartLike)

const toolName = computed(() => {
  const name = tool.value.toolName || partType.value.replace(/^tool-/, '')
  return name || 'tool'
})

const toolStatusLabel = computed(() => {
  switch (tool.value.state) {
    case 'input-streaming':
      return '正在调用工具…'
    case 'input-available':
      return '工具已就绪'
    case 'approval-requested':
    case 'approval-responded':
      return '等待审批'
    case 'output-available':
      return '工具完成'
    case 'output-error':
      return '工具失败'
    case 'output-denied':
      return '工具被拒绝'
    default:
      return '工具'
  }
})

const toolStatusClass = computed(() => {
  switch (tool.value.state) {
    case 'output-error':
      return 'bg-rose-50 text-rose-700 ring-rose-200 dark:bg-rose-500/10 dark:text-rose-200 dark:ring-rose-500/30'
    case 'output-denied':
      return 'bg-amber-50 text-amber-800 ring-amber-200 dark:bg-amber-500/10 dark:text-amber-100 dark:ring-amber-500/30'
    case 'output-available':
      return 'bg-emerald-50 text-emerald-700 ring-emerald-200 dark:bg-emerald-500/10 dark:text-emerald-200 dark:ring-emerald-500/30'
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
    class="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 dark:border-dark-700 dark:bg-dark-800"
    data-testid="ai-part-reasoning"
  >
    <summary class="cursor-pointer select-none text-xs font-bold text-slate-500 dark:text-accent-300">
      {{ reasoningStreaming ? '思考中…' : '思考过程' }}
    </summary>
    <p class="mt-2 whitespace-pre-wrap break-words text-xs leading-5 text-slate-600 dark:text-accent-200">
      {{ textPartText }}
    </p>
  </details>

  <!-- 工具调用卡片 -->
  <div
    v-else-if="isToolPart"
    class="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 dark:border-dark-700 dark:bg-dark-800"
    data-testid="ai-part-tool"
  >
    <div class="flex flex-wrap items-center gap-2">
      <span class="text-xs font-black text-slate-700 dark:text-accent-100">{{ toolName }}</span>
      <span class="rounded-full px-2 py-0.5 text-[10px] font-bold ring-1" :class="toolStatusClass">
        {{ toolStatusLabel }}
      </span>
    </div>
    <p v-if="tool.state === 'output-error' && tool.errorText" class="mt-2 break-words text-xs text-rose-600 dark:text-rose-300">
      {{ tool.errorText }}
    </p>
    <details v-if="toolInputJson" class="mt-2">
      <summary class="cursor-pointer select-none text-[11px] font-bold text-slate-500 dark:text-accent-300">输入</summary>
      <pre class="mt-1 overflow-auto whitespace-pre-wrap break-words rounded bg-white p-2 font-mono text-[11px] leading-5 text-slate-700 dark:bg-dark-950 dark:text-accent-200">{{ toolInputJson }}</pre>
    </details>
    <details v-if="toolOutputJson && tool.state !== 'output-error'" class="mt-2">
      <summary class="cursor-pointer select-none text-[11px] font-bold text-slate-500 dark:text-accent-300">输出</summary>
      <pre class="mt-1 overflow-auto whitespace-pre-wrap break-words rounded bg-white p-2 font-mono text-[11px] leading-5 text-slate-700 dark:bg-dark-950 dark:text-accent-200">{{ toolOutputJson }}</pre>
    </details>
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
      调试片段（{{ partType }}）
    </summary>
    <pre class="mt-1 overflow-auto whitespace-pre-wrap break-words font-mono text-[11px] leading-5 text-slate-500 dark:text-accent-300">{{ debugJson }}</pre>
  </details>
</template>
