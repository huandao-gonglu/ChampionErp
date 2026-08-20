<script setup lang="ts">
import { computed } from 'vue'
import { PhPlus } from '@phosphor-icons/vue'
import TaskApprovalModeSelect from './TaskApprovalModeSelect.vue'

const props = defineProps<{
  modelValue: string
  busy: boolean
}>()

const emit = defineEmits<{
  (event: 'update:modelValue', value: string): void
  (event: 'send'): void
  (event: 'stop'): void
}>()

const canSend = computed(() => props.modelValue.trim().length > 0 && !props.busy)

function onInput(event: Event) {
  emit('update:modelValue', (event.target as HTMLTextAreaElement).value)
}

function onKeydown(event: KeyboardEvent) {
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
        <button
          v-else
          type="submit"
          class="btn btn-primary px-3 py-1.5 text-xs"
          :disabled="!canSend"
          data-testid="ai-chat-send"
        >
          发送
        </button>
      </div>
    </div>
    <p class="mt-2 text-xs text-slate-400">
      Enter 发送，Shift + Enter 换行。完全授权只跳过人工审批，不会绕过资料校验或扩大工具权限。
    </p>
  </form>
</template>
