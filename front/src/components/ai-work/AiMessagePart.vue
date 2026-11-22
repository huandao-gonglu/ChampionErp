<script setup lang="ts">
import { computed, ref } from 'vue'
import { PhBrain, PhCaretRight } from '@phosphor-icons/vue'
import type { AiUiPart } from '@/types/aiWork'
import { describeAiTool } from '@/utils/aiToolLabels'
import { aiToolIcon } from '@/utils/aiToolIcons'
import { splitAssistantText } from '@/utils/aiThinkingPresentation'
import { aiToolName, aiToolStatus, desktopImageAction, isAiToolPart, toolNeedsAttention, type ToolPartView } from '@/utils/aiMessagePresentation'
import { launchDesktop } from '@/utils/desktopHandoff'
import { hasLocalOperations } from '@/utils/desktop'
import { continueImageOperation } from '@/api/draftImages'
import { withAiConversation } from '@/services/withAiConversation'

const props = defineProps<{
  part: AiUiPart
  pendingKind?: 'approval' | 'external' | 'input'
  progress?: string
  active?: boolean
}>()

const partType = computed(() => (props.part as { type: string }).type)
const expanded = ref<Record<string, boolean>>({})
function toggleDetails(event: Event, key: string) {
  expanded.value[key] = (event.target as HTMLDetailsElement).open
}

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
const toolStatusLabel = computed(() => statusLabels[status.value])
const imageAction = computed(() => desktopImageAction(props.part))
const imageTargetLabel = computed(() => {
  if (!imageAction.value) return ''
  const input = tool.value.input && typeof tool.value.input === 'object' ? tool.value.input as Record<string, unknown> : undefined
  const output = tool.value.output && typeof tool.value.output === 'object'
    ? tool.value.output as { error?: { details?: { request?: Record<string, unknown> } } }
    : undefined
  const draftId = input?.draft_id ?? output?.error?.details?.request?.draft_id
  return typeof draftId === 'string' && draftId.trim() ? `草稿 ${draftId.trim()}` : ''
})
const continuing = ref(false)
const continueError = ref('')
async function continueImage() {
  const action = imageAction.value
  if (!action || continuing.value) return
  continuing.value = true
  continueError.value = ''
  try {
    if (hasLocalOperations()) await withAiConversation('继续 AI 图片处理', id => continueImageOperation(action, id))
    else await launchDesktop({ target: '/aiWork', state: { imageOperation: action } })
  } catch (error) {
    continueError.value = error instanceof Error ? error.message : '暂时无法继续图片处理。'
  } finally {
    continuing.value = false
  }
}
// 原生脚本诊断供模型修正和人工展开查看；运行时失败状态仍保留，不能据此断言没有写入。
const scriptDiagnostic = computed(() => (
  aiToolName(props.part) === 'run_code' && tool.value.state === 'output-error'
    && /^(?:(?:Type|Syntax) error in code:|Runtime error:)\r?\n/.test(tool.value.errorText || '')
    ? tool.value.errorText : ''
))
const toolError = computed(() => (
  scriptDiagnostic.value ? businessResult.value?.error?.message || ''
    : tool.value.errorText || businessResult.value?.error?.message || ''
))

const toolStatusClass = computed(() => {
  switch (status.value) {
    case 'failed':
      return 'bg-rose-50 text-rose-700 ring-rose-200 dark:bg-rose-500/10 dark:text-rose-200 dark:ring-rose-500/30'
    case 'unknown':
    case 'partial':
    case 'input':
    case 'approval':
    case 'desktop':
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
// 只移除显示末尾的空白，后续正文到达时中间空白仍完整保留；不修改原生消息。
const textPartText = computed(() => ((props.part as unknown as { text?: string }).text || '').trimEnd())

const reasoningStreaming = computed(() => (
  (props.part as unknown as { state?: string }).state === 'streaming'
))
const textSegments = computed(() => splitAssistantText(textPartText.value, reasoningStreaming.value))

const attentionMessages: Record<string, string> = {
  unknown: '尚未收到明确结果，请核对实际状态。',
  failed: '此次操作未成功，请查看处理结果。',
  partial: '部分操作尚未完成，请查看处理结果。',
  denied: '此次操作未获批准。',
}

const statusLabels: Record<string, string> = {
  preparing: '正在准备…',
  running: '处理中',
  approval: '等待确认',
  input: '等待回复',
  waiting: '等待结果',
  unknown: '结果未确认',
  failed: '操作失败',
  partial: '部分完成',
  denied: '未获批准',
  completed: '已完成',
  submitted: '已提交',
  script_error: '脚本检查未通过',
  desktop: '等待桌面',
}
</script>

<template>
  <!-- 文本 -->
  <template v-if="partType === 'text'">
    <template v-for="(segment, index) in textSegments" :key="index">
      <p v-if="segment.kind === 'text'" class="whitespace-pre-wrap break-words text-sm leading-6" data-testid="ai-part-text">{{ segment.text }}</p>
      <details v-else class="group/reasoning min-w-0" :data-testid="segment.kind === 'thinking' ? 'ai-part-reasoning' : `ai-part-${segment.kind}`" @toggle="toggleDetails($event, `segment-${index}`)">
        <summary class="flex cursor-pointer list-none items-center gap-2 py-1 text-xs text-slate-500 focus-visible:outline-primary-500 dark:text-accent-300 [&::-webkit-details-marker]:hidden">
          <PhBrain :size="16" class="shrink-0" aria-hidden="true" />
          <template v-if="segment.kind === 'tool-markup'">查看未执行的工具文本</template>
          <template v-else>{{ reasoningStreaming ? '正在思考…' : (segment.kind === 'thinking' ? '思考过程' : '查看回复原文（含思考内容）') }}</template>
          <PhCaretRight :size="12" class="shrink-0 transition-transform group-open/reasoning:rotate-90" aria-hidden="true" />
        </summary>
        <template v-if="expanded[`segment-${index}`]">
          <p v-if="segment.kind === 'unseparated' && !reasoningStreaming" class="py-1 pl-6 text-xs text-slate-500 dark:text-accent-300">接口未分开思考与答复，完整内容保留在这里。</p>
          <p class="py-1 pl-6 whitespace-pre-wrap break-words text-xs leading-5 text-slate-600 dark:text-accent-200">{{ segment.text }}</p>
        </template>
      </details>
    </template>
  </template>

  <!-- 思考过程：默认折叠，流式时显示进行中 -->
  <details
    v-else-if="partType === 'reasoning'"
    class="group/reasoning min-w-0"
    data-testid="ai-part-reasoning"
    @toggle="toggleDetails($event, 'reasoning')"
  >
    <summary class="flex cursor-pointer list-none items-center gap-2 py-1 text-xs text-slate-500 focus-visible:outline-primary-500 dark:text-accent-300 [&::-webkit-details-marker]:hidden">
      <PhBrain :size="16" class="shrink-0" aria-hidden="true" />
      {{ reasoningStreaming ? '思考中…' : '思考过程' }}
      <PhCaretRight :size="12" class="shrink-0 transition-transform group-open/reasoning:rotate-90" aria-hidden="true" />
    </summary>
    <p v-if="expanded.reasoning" class="py-1 pl-6 whitespace-pre-wrap break-words text-xs leading-5 text-slate-600 dark:text-accent-200">
      {{ textPartText }}
    </p>
  </details>

  <!-- 工具在原位置更新；仅展开时展示参数和回执。 -->
  <div v-else-if="isToolPart" class="min-w-0" data-testid="ai-part-tool">
    <details data-testid="ai-tool-details" class="group/tool" @toggle="toggleDetails($event, 'tool')">
      <summary class="flex cursor-pointer list-none items-center gap-2 rounded py-1 text-xs focus-visible:outline-primary-500 [&::-webkit-details-marker]:hidden">
        <component :is="toolIcon" :size="16" class="shrink-0 text-slate-500 dark:text-accent-300" aria-hidden="true" />
        <span class="min-w-0 break-words text-slate-600 dark:text-accent-200">{{ toolName }}<template v-if="imageTargetLabel"> · {{ imageTargetLabel }}</template></span>
        <span
          class="shrink-0 rounded px-1.5 py-0.5 text-[10px]"
          :class="toolStatusClass"
          :role="['preparing', 'waiting', 'running'].includes(status) ? 'status' : undefined"
        >{{ toolStatusLabel }}</span>
        <PhCaretRight :size="12" class="shrink-0 text-slate-400 transition-transform group-open/tool:rotate-90" aria-hidden="true" />
      </summary>
      <div v-if="expanded.tool" class="ml-3 space-y-2 border-l border-slate-200 py-2 pl-3 dark:border-dark-700">
        <template v-if="toolInputJson">
          <p class="text-xs text-slate-500">操作详情</p>
          <pre class="max-h-64 overflow-auto whitespace-pre-wrap break-words text-[11px] leading-5 text-slate-600 dark:text-accent-200">{{ toolInputJson }}</pre>
        </template>
        <template v-if="toolOutputJson">
          <p class="text-xs text-slate-500">处理结果</p>
          <pre class="max-h-64 overflow-auto whitespace-pre-wrap break-words text-[11px] leading-5 text-slate-600 dark:text-accent-200">{{ toolOutputJson }}</pre>
        </template>
        <template v-if="scriptDiagnostic">
          <p class="text-xs text-slate-500">脚本诊断</p>
          <pre data-testid="ai-script-diagnostic" class="max-h-64 overflow-auto whitespace-pre-wrap break-words text-[11px] leading-5 text-slate-600 dark:text-accent-200">{{ scriptDiagnostic }}</pre>
        </template>
      </div>
    </details>
    <p v-if="progress" role="status" class="ml-3 mt-1 text-xs text-slate-500 dark:text-accent-300">{{ progress }}</p>
    <div v-if="imageAction" class="ml-3 mt-1 space-y-1">
      <p class="text-xs text-amber-800 dark:text-amber-200">{{ toolError }}</p>
      <button type="button" class="btn btn-outline btn-sm" :disabled="continuing" @click="continueImage">在桌面继续</button>
      <p v-if="continueError" role="alert" class="text-xs text-rose-700 dark:text-rose-300">{{ continueError }}</p>
    </div>
    <p v-else-if="toolError" class="ml-3 mt-1 break-words text-xs text-rose-700 dark:text-rose-300" role="alert">
      {{ toolError }}
    </p>
    <p v-else-if="toolNeedsAttention(status) && status !== 'approval'" class="ml-3 mt-1 text-xs text-amber-800 dark:text-amber-200">
      {{ attentionMessages[status] }}
    </p>
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
    <img
      v-if="/^data:image\/(?:png|jpeg|webp);base64,/i.test(fileUrl)"
      :src="fileUrl"
      :alt="fileName"
      class="max-h-56 max-w-full rounded object-contain"
      loading="lazy"
    />
    <a
      v-else-if="isSafeFileUrl"
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
    @toggle="toggleDetails($event, 'debug')"
  >
    <summary class="cursor-pointer select-none text-[11px] font-bold text-slate-400">
      详细信息
    </summary>
    <pre v-if="expanded.debug" class="mt-1 overflow-auto whitespace-pre-wrap break-words font-mono text-[11px] leading-5 text-slate-500 dark:text-accent-300">{{ debugJson }}</pre>
  </details>
</template>
