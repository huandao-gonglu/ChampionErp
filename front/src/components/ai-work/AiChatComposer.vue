<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { PhPlus } from '@phosphor-icons/vue'
import AiChatCommandPanel from './AiChatCommandPanel.vue'
import TaskApprovalModeSelect from './TaskApprovalModeSelect.vue'
import { useChatCommands } from '@/composables/useChatCommands'

const props = withDefaults(defineProps<{
  modelValue: string
  busy: boolean
  /** 非空时锁定普通发送（例如存在未解决的全局任务），并展示原因。 */
  sendDisabledReason?: string
}>(), {
  sendDisabledReason: '',
})

const emit = defineEmits<{
  (event: 'update:modelValue', value: string): void
  (event: 'send'): void
  (event: 'stop'): void
}>()

const { commandsFor, selectCommand } = useChatCommands()

const canSend = computed(() => (
  props.modelValue.trim().length > 0 && !props.busy && !props.sendDisabledReason
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
          :disabled="busy"
          data-testid="ai-chat-input"
          @input="onInput"
          @keydown="onKeydown"
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
            <TaskApprovalModeSelect />
          </div>
          <button
            v-if="busy"
            type="button"
            class="btn btn-outline px-3 py-1.5 text-xs"
            data-testid="ai-chat-stop"
            @click="emit('stop')"
          >
            停止
          </button>
          <template v-else>
            <button
              type="submit"
              class="btn btn-primary px-3 py-1.5 text-xs"
              :disabled="!canSend"
              data-testid="ai-chat-send"
            >
              发送
            </button>
          </template>
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
      Enter 发送，Shift + Enter 换行，输入 / 查看可用命令。完全授权只跳过人工审批，不会绕过资料校验或扩大工具权限。
    </p>
  </form>
</template>
