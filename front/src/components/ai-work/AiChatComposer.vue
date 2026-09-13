<script setup lang="ts">
import { computed, onDeactivated, ref, watch } from 'vue'
import { useEventListener } from '@vueuse/core'
import { PhEye, PhEyeSlash, PhPlus } from '@phosphor-icons/vue'
import AiChatCommandPanel from './AiChatCommandPanel.vue'
import { useChatCommands } from '@/composables/useChatCommands'
import { useAiPageContextStore } from '@/stores/aiPageContext'

const props = withDefaults(defineProps<{
  modelValue: string
  busy: boolean
  stopping?: boolean
  conversationId?: string
  /** 非空时锁定普通发送（例如存在未解决的全局任务），并展示原因。 */
  sendDisabledReason?: string
}>(), {
  sendDisabledReason: '',
  conversationId: '',
})

const emit = defineEmits<{
  (event: 'update:modelValue', value: string): void
  (event: 'send'): void
  (event: 'stop'): void
}>()

const { commandsFor, selectCommand } = useChatCommands()
const pageContext = useAiPageContextStore()
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

const canSend = computed(() => (
  props.modelValue.trim().length > 0 && !props.sendDisabledReason && !props.stopping && !composing.value
))

/** 输入以 `/` 开头时返回其后的查询串，否则返回 null。 */
const commandQuery = computed(() => (
  props.modelValue.startsWith('/') ? props.modelValue.slice(1) : null
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
  && !props.busy
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
    class="shrink-0 border-t border-slate-200 bg-white pt-4 dark:border-dark-700 dark:bg-dark-900"
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
      <div class="rounded-2xl border border-slate-200 bg-white px-3 pb-2.5 pt-2 shadow-sm transition focus-within:border-primary-300 focus-within:ring-2 focus-within:ring-primary-200/60 dark:border-dark-600 dark:bg-dark-800 dark:focus-within:border-primary-500/60 dark:focus-within:ring-primary-500/15">
        <label class="sr-only" for="ai-chat-input">给全局 Agent 的消息</label>
        <textarea
          id="ai-chat-input"
          :value="modelValue"
          class="min-h-20 w-full resize-y bg-transparent px-1 py-2 text-sm text-slate-900 outline-none placeholder:text-slate-400 disabled:cursor-not-allowed disabled:opacity-60 dark:text-white dark:placeholder:text-accent-400"
          placeholder="输入消息，向全局 Agent 提问"
          data-testid="ai-chat-input"
          @input="onInput"
          @keydown="onKeydown"
          @compositionstart="onCompositionStart"
          @compositionend="composing = false"
        ></textarea>
        <div class="mt-1 flex items-center justify-between gap-3">
          <div class="flex min-w-0 items-center gap-1">
            <button
              type="button"
              class="flex size-9 shrink-0 items-center justify-center rounded-xl text-slate-500 transition hover:bg-slate-100 hover:text-slate-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-400/70 disabled:cursor-not-allowed disabled:opacity-50 dark:text-accent-300 dark:hover:bg-dark-700 dark:hover:text-white"
              aria-label="添加文件（暂未开放）"
              title="添加文件（暂未开放）"
              data-testid="ai-chat-add-file"
              disabled
            >
              <PhPlus :size="24" weight="regular" />
            </button>
            <button
              type="button"
              class="flex size-9 shrink-0 items-center justify-center rounded-xl transition hover:bg-slate-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-400/70 dark:hover:bg-dark-700"
              :class="pageContext.enabled ? 'text-primary-600 dark:text-primary-300' : 'text-slate-400 dark:text-accent-400'"
              aria-label="读取背景"
              :aria-pressed="pageContext.enabled"
              :title="pageContext.hint"
              data-testid="ai-chat-background-toggle"
              @click="pageContext.toggle"
            >
              <PhEye v-if="pageContext.enabled" :size="22" aria-hidden="true" />
              <PhEyeSlash v-else :size="22" aria-hidden="true" />
            </button>
          </div>
          <div class="flex items-center gap-2">
            <button
              type="submit"
              class="btn btn-primary px-3 py-1.5 text-xs"
              :disabled="!canSend"
              data-testid="ai-chat-send"
            >
              发送
            </button>
            <button
              v-if="busy"
              type="button"
              class="btn btn-outline px-3 py-1.5 text-xs"
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
      v-if="sendDisabledReason"
      role="status"
      class="mt-2 rounded-lg bg-amber-50 px-3 py-2 text-xs font-semibold text-amber-800 ring-1 ring-amber-200 dark:bg-amber-500/10 dark:text-amber-200 dark:ring-amber-500/30"
      data-testid="ai-chat-send-blocked"
    >
      {{ sendDisabledReason }}
    </p>
    <p class="mt-2 text-xs text-slate-400">
      Enter 发送，Shift + Enter 换行，连续按两次 Esc 停止，输入 / 查看可用命令。运行期间也可以发送补充资料或纠正要求。
    </p>
  </form>
</template>
