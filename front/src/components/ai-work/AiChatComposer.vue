<script setup lang="ts">
import { useAiChatStore } from '@/stores/aiChat'
import { chatImageAccept } from '@/composables/useChatImages'
import { modelOperationReason } from '@/utils/desktop'
import { computed, onDeactivated, ref, watch } from 'vue'
import { useEventListener } from '@vueuse/core'
import { PhEye, PhEyeSlash, PhListDashes, PhPlus } from '@phosphor-icons/vue'
import AiChatCommandPanel from './AiChatCommandPanel.vue'
import AiApprovalModeSelect from './AiApprovalModeSelect.vue'
import WorkspaceHint from '@/components/shared/WorkspaceHint.vue'
import { useChatCommands } from '@/composables/useChatCommands'
import { useAiPageContextStore } from '@/stores/aiPageContext'

const props = withDefaults(defineProps<{
  modelValue: string
  busy: boolean
  stopping?: boolean
  queuedMessages?: { conversation_id?: string; status?: string; message_id: string; text: string }[]
  statusNotice?: string
  conversationId?: string
  /** 非空时锁定普通发送（例如存在未解决的全局任务），并展示原因。 */
  sendDisabledReason?: string
  /** 全页居中布局下省去通栏顶分隔线，减少视觉切分。 */
  borderless?: boolean
}>(), {
  queuedMessages: () => [],
  statusNotice: '',
  sendDisabledReason: '',
  conversationId: '',
  borderless: false,
})

const emit = defineEmits<{
  (event: 'update:modelValue', value: string): void
  (event: 'send'): void
  (event: 'stop'): void
}>()

const { commandsFor, selectCommand } = useChatCommands()
const pageContext = useAiPageContextStore()
const chatStore = useAiChatStore()
const picker = ref<HTMLInputElement>()
function onPick(event: Event) {
  const element = event.target as HTMLInputElement
  void chatStore.images.add(Array.from(element.files ?? []))
  element.value = ''
}
function onPaste(event: ClipboardEvent) {
  const clipboard = event.clipboardData
  if (!clipboard) return
  const files = clipboard.files.length ? Array.from(clipboard.files) : Array.from(clipboard.items)
    .map(item => item.getAsFile()).filter((file): file is File => file !== null)
  if (!files.length) return
  event.preventDefault()
  void chatStore.images.add(files)
}
const composing = ref(false)
const stopArmed = ref(false)

function resetStopShortcut(): void {
  stopArmed.value = false
}

function requestStop(): void {
  resetStopShortcut()
  if (props.busy && !props.stopping) emit('stop')
}

function onCompositionStart(): void {
  composing.value = true
  resetStopShortcut()
}

function isCompositionKey(event: KeyboardEvent): boolean {
  // IME 结束组合时可能先触发 compositionend，此时需用 keyCode 229 补充判断。
  return composing.value || event.isComposing || event.keyCode === 229
}

function onComposerKeydown(event: KeyboardEvent): void {
  if (isCompositionKey(event)) {
    resetStopShortcut()
    if (event.key === 'Escape') event.stopPropagation()
    return
  }
  if (event.key !== 'Escape' || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) {
    resetStopShortcut()
    return
  }
  if (!props.busy) return
  // 运行期间的 Esc 属于停止快捷键，不能同时收起外层浮动对话。
  event.preventDefault()
  event.stopPropagation()
  if (props.stopping || event.repeat) return
  if (stopArmed.value) requestStop()
  else stopArmed.value = true
}

watch(() => [props.busy, props.stopping, props.conversationId], resetStopShortcut, { flush: 'sync' })
useEventListener(window, 'blur', resetStopShortcut)
useEventListener(document, 'visibilitychange', resetStopShortcut)
onDeactivated(resetStopShortcut)

const blockedReason = computed(() => (!chatStore.images.attachments.length && props.modelValue.trim() === '/resume') ? '' : props.sendDisabledReason || modelOperationReason('global.chat') || (chatStore.images.attachments.length ? chatStore.images.blocked : ''))
const canSend = computed(() => (
  (props.modelValue.trim().length > 0 || chatStore.images.attachments.length > 0) && !chatStore.images.reading && !blockedReason.value && !props.stopping && !composing.value
))

/** 输入以 `/` 开头时返回其后的查询串，否则返回 null。 */
const commandQuery = computed(() => (
  !chatStore.images.attachments.length && props.modelValue.startsWith('/') ? props.modelValue.slice(1) : null
))
const matchedCommandEntries = computed(() => (
  commandQuery.value === null ? [] : commandsFor(commandQuery.value)
))
/** 可执行条目在匹配列表中的下标；键盘导航只在可执行项之间移动。 */
const enabledCommandIndexes = computed(() => matchedCommandEntries.value
  .map((entry, index) => (entry.enabled ? index : -1))
  .filter((index) => index >= 0))
const activeCommandIndex = ref(0)
const commandPanelDismissed = ref(false)
const commandPanelVisible = computed(() => (
  commandQuery.value !== null
  && matchedCommandEntries.value.length > 0
  && !commandPanelDismissed.value
  && (!props.busy || matchedCommandEntries.value.some(entry => entry.command.name === 'resume'))
))

// 查询串变化（继续输入或删除 `/`）时把高亮重置为第一个可执行项，并恢复被 Esc 关闭的面板。
watch(commandQuery, () => {
  activeCommandIndex.value = enabledCommandIndexes.value[0] ?? 0
  commandPanelDismissed.value = false
})

function moveActiveCommand(delta: number): void {
  const indexes = enabledCommandIndexes.value
  if (indexes.length === 0) return
  const current = indexes.indexOf(activeCommandIndex.value)
  const next = current === -1
    ? (delta > 0 ? 0 : indexes.length - 1)
    : (current + delta + indexes.length) % indexes.length
  activeCommandIndex.value = indexes[next]
}

function hoverCommand(index: number): void {
  const entry = matchedCommandEntries.value[index]
  if (entry?.enabled) activeCommandIndex.value = index
}

function selectCommandByIndex(index: number): void {
  const entry = matchedCommandEntries.value[index]
  if (entry?.enabled) selectCommand(entry.command)
}

function onInput(event: Event) {
  emit('update:modelValue', (event.target as HTMLTextAreaElement).value)
}

function onKeydown(event: KeyboardEvent) {
  if (isCompositionKey(event)) {
    resetStopShortcut()
    if (event.key === 'Escape') event.stopPropagation()
    return
  }
  if (commandPanelVisible.value) {
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      moveActiveCommand(1)
      return
    }
    if (event.key === 'ArrowUp') {
      event.preventDefault()
      moveActiveCommand(-1)
      return
    }
    if (event.key === 'Escape') {
      event.preventDefault()
      // 阻止冒泡，避免同时触发外层浮层的关闭快捷键。
      event.stopPropagation()
      commandPanelDismissed.value = true
      return
    }
    if ((event.key === 'Enter' || event.key === 'Tab') && !event.shiftKey) {
      event.preventDefault()
      selectCommandByIndex(activeCommandIndex.value)
      return
    }
  }
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault()
    if (canSend.value) emit('send')
  }
}
</script>

<template>
  <form
    class="ai-chat-composer shrink-0 bg-white dark:bg-dark-900"
    :class="borderless ? 'pt-1' : 'border-t border-slate-200/80 pt-2.5 dark:border-dark-800'"
    data-testid="ai-chat-composer"
    @submit.prevent="canSend && emit('send')"
    @keydown="onComposerKeydown"
    @focusout="resetStopShortcut"
  >
    <div class="relative">
      <AiChatCommandPanel
        v-if="commandPanelVisible"
        :entries="matchedCommandEntries"
        :active-index="activeCommandIndex"
        @select="selectCommandByIndex"
        @hover="hoverCommand"
      />
      <div v-if="statusNotice" role="status" class="mx-3 mb-1.5 text-xs text-slate-600 dark:text-accent-300">
        {{ statusNotice }}
      </div>
      <div
        v-if="queuedMessages?.length" role="status" aria-live="polite" aria-label="排队消息"
        data-testid="ai-chat-queue"
        class="mx-3 max-h-28 overflow-y-auto rounded-t-2xl border border-b-0 border-slate-200 bg-slate-50 px-3 py-1 text-sm dark:border-dark-600 dark:bg-dark-700"
      >
        <div v-for="message in queuedMessages" :key="message.message_id" class="flex min-h-8 items-center gap-2">
          <PhListDashes :size="16" class="shrink-0 text-slate-500 dark:text-accent-400" aria-hidden="true" />
          <span class="min-w-0 flex-1 truncate text-slate-800 dark:text-accent-100" :title="message.text">{{ message.text }}</span>
          <span class="shrink-0 text-xs text-slate-500 dark:text-accent-300">{{ chatStore.queueAction === message.message_id || message.status === 'routing' ? '处理中' : message.status && message.status !== 'queued' ? '发送中' : '排队中' }}</span>
          <button
            v-if="!message.status || message.status === 'queued'" type="button" class="shrink-0 rounded px-1.5 py-1 text-xs font-semibold text-primary-700 hover:underline disabled:opacity-50 dark:text-primary-300"
            :disabled="!!chatStore.queueAction || stopping" @click="chatStore.actOnQueue(message.conversation_id || conversationId, message.message_id, 'send')"
          >
            立即发送
          </button>
          <button
            v-if="!message.status || message.status === 'queued'" type="button" class="shrink-0 rounded px-1.5 py-1 text-xs text-slate-600 hover:underline disabled:opacity-50 dark:text-accent-200"
            :disabled="!!chatStore.queueAction || stopping" @click="chatStore.actOnQueue(message.conversation_id || conversationId, message.message_id, 'delete')"
          >
            删除
          </button>
        </div>
      </div>
      <div class="ai-chat-composer-surface rounded-xl border border-slate-200 bg-slate-50/70 px-3 pb-2 pt-1.5 shadow-sm transition focus-within:border-primary-400 focus-within:bg-white focus-within:ring-2 focus-within:ring-primary-200/50 dark:border-dark-700 dark:bg-dark-800/90 dark:focus-within:border-primary-500/60 dark:focus-within:ring-primary-500/15">
        <div v-if="chatStore.images.attachments.length" class="mb-1.5 flex flex-wrap gap-2">
          <div v-for="(item, index) in chatStore.images.attachments" :key="index" class="relative">
            <img :src="item.part.url" :alt="item.part.filename || '待发送图片'" class="h-14 w-14 rounded-md object-contain" />
            <button type="button" :aria-label="`移除第 ${index + 1} 张图片`" class="absolute right-0 top-0 rounded bg-slate-800 px-1.5 text-white" @click="chatStore.images.attachments.splice(index, 1)">×</button>
          </div>
        </div>
        <p v-if="chatStore.images.error" role="alert" class="text-xs text-rose-600">{{ chatStore.images.error }}</p>
        <p v-if="chatStore.images.reading" role="status" class="text-xs">正在读取图片…</p>
        <input ref="picker" type="file" :accept="chatImageAccept" multiple hidden @change="onPick" />
        <label class="sr-only" for="ai-chat-input">发送消息</label>
        <textarea
          id="ai-chat-input"
          :value="modelValue"
          rows="2"
          class="min-h-11 max-h-48 w-full resize-y bg-transparent px-1 py-1 text-sm leading-5 text-slate-900 outline-none placeholder:text-slate-400 disabled:cursor-not-allowed disabled:opacity-60 dark:text-white dark:placeholder:text-accent-400"
          placeholder="输入问题或描述要处理的事情"
          data-testid="ai-chat-input"
          @paste="onPaste"
          @input="onInput"
          @keydown="onKeydown"
          @compositionstart="onCompositionStart"
          @compositionend="composing = false"
        ></textarea>
        <div class="mt-1 flex flex-wrap items-center justify-between gap-2">
          <div class="flex min-w-0 items-center gap-1">
            <AiApprovalModeSelect />
            <button
              type="button"
              class="flex size-8 shrink-0 items-center justify-center rounded-lg text-slate-500 transition hover:bg-slate-200/70 hover:text-slate-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-400/70 disabled:cursor-not-allowed disabled:opacity-50 dark:text-accent-300 dark:hover:bg-dark-700 dark:hover:text-white"
              aria-label="添加图片"
              :title="chatStore.images.blocked || '添加图片，也可将图片粘贴到输入框'"
              data-testid="ai-chat-add-file"
              :disabled="Boolean(chatStore.images.blocked) || chatStore.images.reading"
              @click="picker?.click()"
            >
              <PhPlus :size="19" weight="regular" />
            </button>
            <WorkspaceHint label="读取背景" :content="pageContext.hint">
              <template #trigger="{ attrs }">
                <button
                  type="button"
                  class="flex size-8 shrink-0 items-center justify-center rounded-lg transition hover:bg-slate-200/70 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-400/70 dark:hover:bg-dark-700"
                  :class="pageContext.enabled ? 'text-primary-600 dark:text-primary-300' : 'text-slate-400 dark:text-accent-400'"
                  aria-label="读取背景"
                  :aria-pressed="pageContext.enabled"
                  v-bind="attrs"
                  data-testid="ai-chat-background-toggle"
                  @click="pageContext.toggle"
                >
                  <PhEye v-if="pageContext.enabled" :size="19" aria-hidden="true" />
                  <PhEyeSlash v-else :size="19" aria-hidden="true" />
                </button>
              </template>
            </WorkspaceHint>
          </div>
          <div class="flex items-center gap-1.5">
            <span class="mr-1 hidden text-[11px] text-slate-400 dark:text-accent-500 sm:inline">
              ai也会犯错，重要内容请仔细核对
            </span>
            <button
              type="submit"
              class="btn btn-primary rounded-lg px-3 py-1 text-xs"
              :disabled="!canSend"
              data-testid="ai-chat-send"
            >
              发送
            </button>
            <button
              v-if="busy"
              type="button"
              class="btn btn-outline rounded-lg px-3 py-1 text-xs"
              data-testid="ai-chat-stop"
              :disabled="stopping"
              :aria-label="stopping ? '正在停止' : stopArmed ? '再次按 Esc 停止当前操作' : '停止当前操作'"
              :title="stopArmed ? '再次按 Esc 停止，焦点变化后取消等待' : '停止当前操作（连续按两次 Esc）'"
              @click="requestStop"
            >
              {{ stopping ? '正在停止…' : stopArmed ? 'Esc' : '停止' }}
            </button>
          </div>
        </div>
      </div>
    </div>
    <p
      v-if="blockedReason"
      role="status"
      class="mt-1.5 rounded-lg bg-amber-50 px-3 py-1.5 text-xs font-semibold text-amber-800 ring-1 ring-amber-200 dark:bg-amber-500/10 dark:text-amber-200 dark:ring-amber-500/30"
      data-testid="ai-chat-send-blocked"
    >
      {{ blockedReason }}
    </p>
  </form>
</template>
