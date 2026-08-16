<script setup lang="ts">
import { computed } from 'vue'

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
    <label class="sr-only" for="ai-chat-input">给全局 Agent 的消息</label>
    <div class="flex items-end gap-3">
      <textarea
        id="ai-chat-input"
        :value="modelValue"
        class="input min-h-20 flex-1 resize-y"
        placeholder="输入消息，向全局 Agent 提问"
        :disabled="busy"
        data-testid="ai-chat-input"
        @input="onInput"
        @keydown="onKeydown"
      ></textarea>
      <button
        v-if="busy"
        type="button"
        class="btn btn-outline mb-0.5"
        data-testid="ai-chat-stop"
        @click="emit('stop')"
      >
        停止
      </button>
      <button
        v-else
        type="submit"
        class="btn btn-primary mb-0.5"
        :disabled="!canSend"
        data-testid="ai-chat-send"
      >
        发送
      </button>
    </div>
    <p class="mt-2 text-xs text-slate-400">
      Enter 发送，Shift + Enter 换行。对话只读查询草稿，不执行写操作。
    </p>
  </form>
</template>
