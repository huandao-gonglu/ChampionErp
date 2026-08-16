<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import AiChatPanel from '@/components/ai-work/AiChatPanel.vue'
import { useAiChatStore } from '@/stores'

const route = useRoute()
const chatStore = useAiChatStore()
const floatingElement = ref<HTMLElement | null>(null)
const pointerWithin = ref(false)
const focusWithin = ref(false)

// AiWork 页面内置完整对话区域，浮动入口只在其他页面显示。
const shouldRender = computed(() => route.name !== 'AiWork')

const panelError = computed(() => chatStore.error?.message || '')

const aiWorkHref = computed(() => {
  const conversationId = chatStore.activeConversationId
  if (!conversationId) return '/aiWork'
  return `/aiWork?conversation_id=${encodeURIComponent(conversationId)}`
})

function handleMouseEnter(): void {
  pointerWithin.value = true
  chatStore.openFloating()
}

function handleMouseLeave(): void {
  pointerWithin.value = false
  if (!focusWithin.value) chatStore.closeFloating()
}

function handleFocusIn(): void {
  focusWithin.value = true
  chatStore.openFloating()
}

function handleFocusOut(event: FocusEvent): void {
  const nextTarget = event.relatedTarget as Node | null
  if (nextTarget && floatingElement.value?.contains(nextTarget)) return
  focusWithin.value = false
  if (!pointerWithin.value) chatStore.closeFloating()
}

watch(shouldRender, (visible) => {
  if (!visible) chatStore.closeFloating()
})

onBeforeUnmount(() => chatStore.closeFloating())
</script>

<template>
  <div
    v-if="shouldRender"
    ref="floatingElement"
    data-testid="ai-work-floating"
    class="fixed bottom-5 right-5 z-[70] flex flex-col items-end gap-3"
    @mouseenter="handleMouseEnter"
    @mouseleave="handleMouseLeave"
    @focusin="handleFocusIn"
    @focusout="handleFocusOut"
    @keydown.esc="chatStore.closeFloating()"
  >
    <section
      v-if="chatStore.floatingOpen"
      id="ai-work-floating-panel"
      role="region"
      aria-label="全局 AI 浮动对话"
      data-testid="ai-work-floating-panel"
      class="flex w-[400px] max-w-[calc(100vw-2.5rem)] flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white text-slate-900 shadow-2xl shadow-slate-950/20 dark:border-dark-700 dark:bg-dark-900 dark:text-white"
    >
      <header class="flex items-center justify-between gap-3 border-b border-slate-200 px-4 py-3 dark:border-dark-700">
        <div class="min-w-0">
          <p class="text-xs font-black uppercase tracking-[0.14em] text-primary-600 dark:text-primary-300">
            全局 AI 对话
          </p>
          <p class="mt-0.5 truncate text-[11px] text-slate-500 dark:text-accent-300">
            {{ chatStore.activeConversationId || '发送第一条消息后自动创建会话' }}
          </p>
        </div>
        <div class="flex shrink-0 items-center">
          <button
            type="button"
            class="flex size-8 items-center justify-center rounded-full text-lg text-slate-400 transition hover:bg-slate-100 hover:text-slate-700 dark:hover:bg-dark-800 dark:hover:text-white"
            aria-label="收起浮动对话"
            data-testid="ai-work-floating-close"
            @click="chatStore.closeFloating()"
          >
            ×
          </button>
        </div>
      </header>

      <div class="h-[480px] max-h-[calc(100vh-180px)] px-4 py-3">
        <AiChatPanel
          :messages="chatStore.messages"
          :busy="chatStore.isBusy"
          :error="panelError"
          :input="chatStore.input"
          @update:input="chatStore.input = $event"
          @send="chatStore.sendMessage()"
          @stop="chatStore.stopStreaming()"
        />
      </div>
    </section>

    <a
      :href="aiWorkHref"
      target="_blank"
      rel="noopener noreferrer"
      aria-label="在新标签页打开 AI Work"
      :aria-expanded="chatStore.floatingOpen"
      aria-controls="ai-work-floating-panel"
      title="在新标签页打开 AI Work"
      data-testid="ai-work-floating-toggle"
      class="flex size-14 items-center justify-center rounded-full border border-white/60 bg-primary-600 text-white shadow-xl shadow-primary-950/25 transition duration-200 hover:-translate-y-1 hover:bg-primary-500 hover:shadow-2xl hover:shadow-primary-950/30 focus:outline-none focus-visible:ring-4 focus-visible:ring-primary-300 dark:border-primary-300/20 dark:bg-primary-500 dark:hover:bg-primary-400"
    >
      <svg
        aria-hidden="true"
        viewBox="0 0 24 24"
        fill="none"
        class="size-7"
        stroke="currentColor"
        stroke-width="1.8"
        stroke-linecap="round"
        stroke-linejoin="round"
      >
        <path d="M7.5 17.5 4 20v-4.3A7.5 7.5 0 0 1 2.5 11C2.5 6.9 6.5 3.5 11.5 3.5S20.5 6.9 20.5 11s-4 7.5-9 7.5c-1.45 0-2.8-.28-4-.78Z" />
        <path d="M8 10.75h7" />
        <path d="M8 13.75h4.5" />
        <path d="M18.5 17.5v3" />
        <path d="M17 19h3" />
      </svg>
    </a>
  </div>
</template>
