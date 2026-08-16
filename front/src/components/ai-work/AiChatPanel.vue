<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import type { UIMessage } from 'ai'
import AiChatComposer from './AiChatComposer.vue'
import AiMessageList from './AiMessageList.vue'

const props = defineProps<{
  messages: UIMessage[]
  busy: boolean
  error?: string
  input: string
}>()

const emit = defineEmits<{
  (event: 'update:input', value: string): void
  (event: 'send'): void
  (event: 'stop'): void
}>()

const scrollRef = ref<HTMLElement | null>(null)

async function scrollToBottom() {
  await nextTick()
  const element = scrollRef.value
  if (element) {
    element.scrollTop = element.scrollHeight
  }
}

watch(() => props.messages, () => {
  void scrollToBottom()
}, { deep: true })
</script>

<template>
  <section class="mx-auto flex h-full w-full max-w-5xl flex-col" data-testid="ai-chat-panel">
    <div ref="scrollRef" class="min-h-0 flex-1 space-y-4 overflow-y-auto pb-5">
      <!-- 空状态 -->
      <div
        v-if="!messages.length"
        class="rounded-2xl border border-dashed border-primary-300 bg-primary-50/60 px-6 py-10 text-center dark:border-primary-500/40 dark:bg-primary-500/10"
        data-testid="ai-chat-empty"
      >
        <div class="mx-auto flex h-12 w-12 items-center justify-center rounded-2xl bg-primary-500 text-xl text-white shadow-lg shadow-primary-500/20">
          ✦
        </div>
        <h3 class="mt-4 text-base font-black">告诉全局 Agent 你想了解什么</h3>
        <p class="mx-auto mt-2 max-w-xl text-sm leading-6 text-slate-500 dark:text-accent-300">
          可以查询草稿、来源平台与目标市场等只读信息；发布等写操作请使用对应的全局任务流程。
        </p>
      </div>

      <!-- 消息气泡 -->
      <AiMessageList v-else :messages="messages" />

      <!-- 流式状态提示 -->
      <p
        v-if="busy"
        class="text-xs text-slate-400"
        data-testid="ai-chat-streaming"
      >
        全局 Agent 正在回复…
      </p>

      <!-- 错误提示 -->
      <p
        v-if="error"
        role="alert"
        class="rounded-xl bg-rose-50 p-4 text-sm text-rose-700 ring-1 ring-rose-200 dark:bg-rose-500/10 dark:text-rose-200 dark:ring-rose-500/30"
        data-testid="ai-chat-error"
      >
        {{ error }}
      </p>
    </div>

    <!-- 输入框 -->
    <AiChatComposer
      :model-value="input"
      :busy="busy"
      @update:model-value="emit('update:input', $event)"
      @send="emit('send')"
      @stop="emit('stop')"
    />
  </section>
</template>
