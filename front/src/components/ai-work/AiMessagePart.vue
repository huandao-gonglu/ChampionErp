<script setup lang="ts">
import { computed, inject, ref } from 'vue'
import { routerKey } from 'vue-router'
import { PhBrain, PhCaretRight, PhWarningCircle } from '@phosphor-icons/vue'
import type { AiUiPart, BusinessWriteReceipt, CodeProgressFatalError, CodeProgressSnapshot } from '@/types/aiWork'
import { describeAiTool, describeRunCodeAction, formatCodeProgress } from '@/utils/aiToolLabels'
import { aiToolIcon } from '@/utils/aiToolIcons'
import { splitAssistantText } from '@/utils/aiThinkingPresentation'
import {
  aiToolName,
  aiToolStatus,
  desktopImageAction,
  fatalModelErrorInfo,
  isAiToolPart,
  toolNeedsAttention,
  type ToolPartView,
} from '@/utils/aiMessagePresentation'
import { launchDesktop } from '@/utils/desktopHandoff'
import { hasLocalOperations } from '@/utils/desktop'
import { continueImageOperation } from '@/api/draftImages'
import { withAiConversation } from '@/services/withAiConversation'

const props = defineProps<{
  part: AiUiPart
  pendingKind?: 'approval' | 'external' | 'input'
  progress?: string
  codeProgress?: CodeProgressSnapshot
  writeReceipts?: BusinessWriteReceipt[]
  stepElapsedSeconds?: number
  active?: boolean
}>()

const partType = computed(() => (props.part as { type: string }).type)
const expanded = ref<Record<string, boolean>>({})
function toggleDetails(event: Event, key: string) {
  expanded.value[key] = (event.target as HTMLDetailsElement).open
}

const isToolPart = computed(() => isAiToolPart(props.part))
const tool = computed(() => props.part as ToolPartView)
const rawToolName = computed(() => aiToolName(props.part))
const isRunCode = computed(() => rawToolName.value === 'run_code')
const toolName = computed(() => (
  isRunCode.value
    ? describeRunCodeAction(tool.value.input, props.codeProgress, props.writeReceipts, tool.value.toolCallId)
    : describeAiTool(rawToolName.value)
))
const toolIcon = computed(() => aiToolIcon(rawToolName.value))

const businessResult = computed(() => (
  tool.value.output && typeof tool.value.output === 'object'
    ? tool.value.output as { ok?: boolean; status?: string; error?: { message?: string } }
    : undefined
))
const status = computed(() => aiToolStatus(props.part, props.pendingKind, props.active))
const isActiveStatus = computed(() => ['preparing', 'waiting', 'running'].includes(status.value))
const toolStatusLabel = computed(() => statusLabels[status.value])
const stepElapsedText = computed(() => {
  if (!isActiveStatus.value || !props.stepElapsedSeconds || props.stepElapsedSeconds < 2) return ''
  const seconds = Math.floor(props.stepElapsedSeconds)
  const minutes = Math.floor(seconds / 60)
  return minutes ? `${minutes}分 ${seconds % 60}秒` : `${seconds}秒`
})
const liveProgress = computed(() => (
  props.progress || (isActiveStatus.value ? formatCodeProgress(props.codeProgress) : '')
))
const liveFatalErrors = computed<CodeProgressFatalError[]>(() => (
  isActiveStatus.value && Array.isArray(props.codeProgress?.fatal_errors)
    ? props.codeProgress.fatal_errors
    : []
))
const fatalModelError = computed(() => fatalModelErrorInfo(props.part))
const fatalModelTitle = computed(() => {
  const err = fatalModelError.value
  if (!err) return ''
  if (err.code === 'AI_PROVIDER_PAYMENT_REQUIRED' || err.statusCode === 402) return 'AI 模型余额不足或计费不可用'
  return 'AI 模型鉴权失败或无访问权限'
})
const router = inject(routerKey, undefined)
function openAiModelSettings(section: 'ai_models' | 'ai_bindings' = 'ai_bindings') {
  if (router) {
    void router.push({ path: '/', query: { tab: 'auth', auth_section: section } })
  } else if (typeof window !== 'undefined') {
    window.location.assign(`/?tab=auth&auth_section=${section}`)
  }
}
function fatalErrorHeading(err: CodeProgressFatalError): string {
  if (err.code === 'AI_PROVIDER_PAYMENT_REQUIRED' || err.status_code === 402) return '模型余额不足'
  return '模型鉴权失败'
}
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

const runCodeSource = computed(() => {
  if (!isRunCode.value || !tool.value.input || typeof tool.value.input !== 'object') return ''
  const code = (tool.value.input as { code?: unknown }).code
  return typeof code === 'string' ? code.trim() : ''
})
const nestedReceiptSummary = computed(() => {
  if (!isRunCode.value || !tool.value.toolCallId || !props.writeReceipts?.length) return []
  const prefix = `${tool.value.toolCallId}__`
  const counts = new Map<string, { label: string; total: number; failed: number }>()
  for (const receipt of props.writeReceipts) {
    if (!receipt.tool_call_id.startsWith(prefix)) continue
    const entry = counts.get(receipt.tool_name) ?? { label: describeAiTool(receipt.tool_name), total: 0, failed: 0 }
    entry.total += 1
    if (receipt.output?.ok === false) entry.failed += 1
    counts.set(receipt.tool_name, entry)
  }
  return [...counts.values()]
})
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
  waiting: '执行中',
  unknown: '结果未确认',
  failed: '操作失败',
  partial: '部分完成',
  denied: '未获批准',
  completed: '已完成',
  submitted: '已提交',
  script_error: '重新调整',
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

  <!-- 致命模型基础设施故障（欠费 / 鉴权失败）：以醒目红色告警卡外显，避免被折叠或绿标掩盖 -->
  <div
    v-else-if="isToolPart && fatalModelError"
    class="min-w-0 rounded-lg border border-rose-200 bg-rose-50/90 px-3 py-2.5 text-xs text-rose-900 dark:border-rose-500/40 dark:bg-rose-500/10 dark:text-rose-100"
    role="alert"
    data-testid="ai-model-infra-alert"
  >
    <div class="flex flex-wrap items-center justify-between gap-2">
      <div class="flex min-w-0 items-center gap-1.5 font-semibold text-rose-800 dark:text-rose-200">
        <PhWarningCircle :size="16" class="shrink-0 text-rose-600 dark:text-rose-300" aria-hidden="true" />
        <span>{{ fatalModelTitle }}</span>
        <span
          v-if="fatalModelError.useCaseLabel || fatalModelError.modelName"
          class="rounded bg-rose-100 px-1.5 py-0.5 text-[10px] font-medium text-rose-700 dark:bg-rose-500/20 dark:text-rose-200"
        >
          {{ [fatalModelError.useCaseLabel, fatalModelError.modelName].filter(Boolean).join(' · ') }}
        </span>
        <span
          v-if="fatalModelError.affectedCount > 1"
          class="rounded bg-rose-200/70 px-1.5 py-0.5 text-[10px] font-medium text-rose-800 dark:bg-rose-500/30 dark:text-rose-100"
          data-testid="ai-model-infra-alert-count"
        >
          影响 {{ fatalModelError.affectedCount }} 次调用
        </span>
      </div>
      <div class="flex shrink-0 flex-wrap items-center gap-2">
        <button
          type="button"
          class="rounded border border-rose-300 bg-white px-2 py-0.5 text-[11px] font-medium text-rose-700 hover:bg-rose-100 dark:border-rose-500/50 dark:bg-dark-900 dark:text-rose-200 dark:hover:bg-rose-500/20"
          data-testid="ai-model-alert-open-bindings"
          @click="openAiModelSettings('ai_bindings')"
        >
          切换功能绑定
        </button>
        <button
          type="button"
          class="rounded border border-rose-200 bg-transparent px-2 py-0.5 text-[11px] text-rose-700 hover:bg-rose-100/70 dark:border-rose-500/40 dark:text-rose-200 dark:hover:bg-rose-500/20"
          data-testid="ai-model-alert-open-models"
          @click="openAiModelSettings('ai_models')"
        >
          检查 AI 模型
        </button>
      </div>
    </div>
    <p class="mt-1.5 break-words leading-5 text-rose-800 dark:text-rose-100">{{ fatalModelError.message || toolError }}</p>
  </div>

  <!-- 工具在原位置更新；仅展开时展示参数和回执。 -->
  <div v-else-if="isToolPart" class="min-w-0" data-testid="ai-part-tool">
    <details data-testid="ai-tool-details" class="group/tool" @toggle="toggleDetails($event, 'tool')">
      <summary class="flex cursor-pointer list-none items-center gap-2 rounded py-1 text-xs focus-visible:outline-primary-500 [&::-webkit-details-marker]:hidden">
        <component :is="toolIcon" :size="16" class="shrink-0 text-slate-500 dark:text-accent-300" aria-hidden="true" />
        <span class="min-w-0 break-words text-slate-600 dark:text-accent-200">{{ toolName }}<template v-if="imageTargetLabel"> · {{ imageTargetLabel }}</template></span>
        <span
          class="inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-[10px]"
          :class="toolStatusClass"
          :role="isActiveStatus ? 'status' : undefined"
        >
          <span v-if="isActiveStatus" class="size-1.5 shrink-0 rounded-full bg-current motion-safe:animate-pulse" aria-hidden="true" />
          {{ toolStatusLabel }}
        </span>
        <span
          v-if="stepElapsedText"
          class="shrink-0 text-[11px] tabular-nums text-slate-400 dark:text-accent-400"
          data-testid="ai-tool-elapsed"
        >{{ stepElapsedText }}</span>
        <PhCaretRight :size="12" class="shrink-0 text-slate-400 transition-transform group-open/tool:rotate-90" aria-hidden="true" />
      </summary>
      <div v-if="expanded.tool" class="ml-3 space-y-2 border-l border-slate-200 py-2 pl-3 dark:border-dark-700">
        <template v-if="nestedReceiptSummary.length">
          <p class="text-xs text-slate-500">子操作回执</p>
          <ul class="space-y-0.5 text-xs text-slate-600 dark:text-accent-200" data-testid="ai-run-code-receipts">
            <li v-for="item in nestedReceiptSummary" :key="item.label">
              {{ item.label }} × {{ item.total }}<span v-if="item.failed" class="text-rose-600 dark:text-rose-300">（{{ item.failed }} 项失败）</span>
            </li>
          </ul>
        </template>
        <template v-if="runCodeSource">
          <p class="text-xs text-slate-500">执行脚本</p>
          <pre data-testid="ai-run-code-source" class="max-h-64 overflow-auto whitespace-pre-wrap break-words rounded bg-slate-50 p-2 font-mono text-[11px] leading-5 text-slate-600 dark:bg-dark-800/70 dark:text-accent-200">{{ runCodeSource }}</pre>
        </template>
        <template v-else-if="toolInputJson">
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
    <p v-if="liveProgress" role="status" class="ml-6 mt-0.5 flex items-center gap-1.5 text-xs text-slate-500 dark:text-accent-300" data-testid="ai-tool-progress">
      <span v-if="isActiveStatus" class="size-1 shrink-0 rounded-full bg-sky-500 motion-safe:animate-pulse" aria-hidden="true" />
      {{ liveProgress }}
    </p>
    <div
      v-if="liveFatalErrors.length"
      class="ml-6 mt-1.5 space-y-1.5"
      data-testid="ai-tool-fatal-alerts"
    >
      <div
        v-for="(fatal, idx) in liveFatalErrors"
        :key="`${fatal.code}-${fatal.use_case_id || ''}-${fatal.model_id || idx}`"
        class="rounded-md border border-rose-200 bg-rose-50/90 px-2.5 py-2 text-xs text-rose-800 dark:border-rose-500/40 dark:bg-rose-500/10 dark:text-rose-100"
        role="alert"
        data-testid="ai-tool-fatal-alert"
      >
        <div class="flex flex-wrap items-center justify-between gap-2">
          <span class="font-semibold">
            {{ fatalErrorHeading(fatal) }}<template v-if="fatal.use_case_label || fatal.model_name"> · {{ [fatal.use_case_label, fatal.model_name].filter(Boolean).join(' / ') }}</template><template v-if="fatal.count && fatal.count > 1">（已出现 {{ fatal.count }} 次）</template>
          </span>
          <button
            type="button"
            class="underline hover:text-rose-950 dark:hover:text-white"
            @click="openAiModelSettings('ai_bindings')"
          >
            前往授权配置 → AI 模型
          </button>
        </div>
        <p v-if="fatal.message" class="mt-1 break-words leading-5">{{ fatal.message }}</p>
      </div>
    </div>
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
