<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { PhPushPin } from '@phosphor-icons/vue'
import AiChatPanel from '@/components/ai-work/AiChatPanel.vue'
import { workflowNavItems } from '@/constants/navigation'
import { useFloatingDrag } from '@/composables/useFloatingDrag'
import { useAiChatStore, useAiWorkDisplayStore } from '@/stores'

const route = useRoute()
const chatStore = useAiChatStore()
const displayStore = useAiWorkDisplayStore()
const floatingElement = ref<HTMLElement | null>(null)
const floatingDrag = useFloatingDrag(floatingElement)
const pointerWithin = ref(false)
const focusWithin = ref(false)
const isPinned = ref(false)
let noticeTimer: ReturnType<typeof setTimeout> | null = null

// AiWork 页面内置完整对话区域，浮动入口只在其他页面显示。
const shouldRender = computed(() => route.name !== 'AiWork')

const foregroundPresentation = computed(() => displayStore.foregroundPresentation)
const presentationError = computed(() => foregroundPresentation.value?.error?.message || '')

const panelError = computed(() => chatStore.error?.message || '')
const terminalNotice = computed(() => displayStore.terminalNotice)

const workspaceReturnQuery = computed(() => {
  if (route.name !== 'WorkflowHome') return {}
  const tab = String(Array.isArray(route.query.tab) ? route.query.tab[0] || '' : route.query.tab || '')
  return workflowNavItems.some((item) => item.key === tab) && tab !== 'dashboard'
    ? { workspace_tab: tab }
    : {}
})

// 普通入口始终指向聊天；任务详情通过独立卡片进入。
const aiWorkRoute = computed(() => {
  return {
    name: 'AiWork',
    query: {
      ...workspaceReturnQuery.value,
      ...(chatStore.conversationLinkId
        ? { conversation_id: chatStore.conversationLinkId }
        : {}),
    },
  }
})

function handleMouseEnter(): void {
  pointerWithin.value = true
  chatStore.openFloating()
}

function handleMouseLeave(): void {
  pointerWithin.value = false
  if (floatingDrag.dragging.value) return
  if (!focusWithin.value && !isPinned.value) chatStore.closeFloating()
}

function handleFocusIn(): void {
  focusWithin.value = true
  chatStore.openFloating()
}

function handleFocusOut(event: FocusEvent): void {
  const nextTarget = event.relatedTarget as Node | null
  if (nextTarget && floatingElement.value?.contains(nextTarget)) return
  focusWithin.value = false
  if (!pointerWithin.value && !isPinned.value) chatStore.closeFloating()
}

function togglePinned(): void {
  isPinned.value = !isPinned.value
}

// 任务结果短暂提示，普通聊天始终保持显示。
watch(terminalNotice, (notice) => {
  if (noticeTimer) {
    clearTimeout(noticeTimer)
    noticeTimer = null
  }
  if (!notice) return
  noticeTimer = setTimeout(() => {
    displayStore.clearTerminalNotice()
    noticeTimer = null
  }, 4000)
})

watch(shouldRender, (visible) => {
  if (!visible) chatStore.closeFloating()
})

onBeforeUnmount(() => {
  if (noticeTimer) clearTimeout(noticeTimer)
  chatStore.closeFloating()
})
</script>

<template>
  <div
    v-if="shouldRender"
    ref="floatingElement"
    data-testid="ai-work-floating"
    class="fixed bottom-5 right-5 z-[70] size-14"
    :style="floatingDrag.style.value"
    @mouseenter="handleMouseEnter"
    @mouseleave="handleMouseLeave"
    @focusin="handleFocusIn"
    @focusout="handleFocusOut"
    @keydown.esc="chatStore.closeFloating()"
  >
    <div
      v-if="chatStore.floatingOpen"
      v-show="!floatingDrag.dragging.value"
      class="absolute"
      data-testid="ai-work-floating-placement"
      :data-side="floatingDrag.panel.value.side"
      :style="floatingDrag.panel.value.style"
    >
      <section
        id="ai-work-floating-panel"
        role="region"
        aria-label="AI 助手"
        data-testid="ai-work-floating-panel"
        class="ai-floating-panel flex h-full w-full min-h-0 flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white/[0.3333] text-slate-900 dark:border-dark-700 dark:bg-dark-900/[0.3333] dark:text-white"
      >
        <header class="flex shrink-0 items-center justify-between gap-3 border-b border-slate-200 px-4 py-3 dark:border-dark-700">
          <div class="min-w-0">
            <p class="text-xs font-black uppercase tracking-[0.14em] text-primary-600 dark:text-primary-300">
              AI 助手
            </p>
            <p class="mt-0.5 truncate text-[11px] text-slate-500 dark:text-accent-300">
              {{ chatStore.restoring ? '正在恢复上次对话…' : chatStore.restoreError ? '上次对话暂时无法读取' : chatStore.activeConversationId || '发送第一条消息后自动创建会话' }}
            </p>
          </div>
          <div class="flex shrink-0 items-center">
            <button
              type="button" class="mr-2 text-xs font-bold text-primary-600 dark:text-primary-300"
              data-testid="ai-work-new-chat" @click="chatStore.startConversation(); chatStore.input = ''"
            >
              新对话
            </button>
            <button
              type="button"
              class="flex size-8 items-center justify-center rounded-full transition hover:bg-slate-100/[var(--ai-chat-surface-opacity,1)] dark:hover:bg-dark-800/[var(--ai-chat-surface-opacity,1)]"
              :class="isPinned
                ? 'bg-primary-50/[var(--ai-chat-surface-opacity,1)] text-primary-600 dark:bg-primary-500/[min(0.15,var(--ai-chat-surface-opacity,1))] dark:text-primary-300'
                : 'text-slate-400 hover:text-slate-700 dark:hover:text-white'"
              :aria-label="isPinned ? '取消钉住浮动对话' : '钉住浮动对话'"
              :aria-pressed="isPinned"
              :title="isPinned ? '取消钉住' : '钉住'"
              data-testid="ai-work-floating-pin"
              @click="togglePinned"
            >
              <PhPushPin :size="17" :weight="isPinned ? 'fill' : 'regular'" />
            </button>
            <button
              type="button"
              class="flex size-8 items-center justify-center rounded-full text-lg text-slate-400 transition hover:bg-slate-100/[var(--ai-chat-surface-opacity,1)] hover:text-slate-700 dark:hover:bg-dark-800/[var(--ai-chat-surface-opacity,1)] dark:hover:text-white"
              aria-label="收起浮动对话"
              data-testid="ai-work-floating-close"
              @click="chatStore.closeFloating()"
            >
              ×
            </button>
          </div>
        </header>

        <aside
          v-if="foregroundPresentation" aria-label="AI 任务进度"
          class="shrink-0 border-b border-slate-200 px-4 py-3 text-xs dark:border-dark-700"
          data-testid="ai-work-task-progress"
        >
          <div class="flex items-center justify-between gap-3">
            <span>{{ foregroundPresentation.displayTitle || 'AI 任务' }} · {{ displayStore.presentationStatusText }}</span>
            <RouterLink
              class="shrink-0 font-bold text-primary-600 dark:text-primary-300"
              data-testid="ai-work-task-details"
              :to="{ name: 'AiWork', query: { ...workspaceReturnQuery, conversation_id: foregroundPresentation.conversationId, presentation_id: foregroundPresentation.presentationId } }"
            >
              查看进度
            </RouterLink>
          </div>
          <p
            v-if="presentationError" role="alert" class="mt-2 text-rose-600 dark:text-rose-300"
            data-testid="ai-work-presentation-error"
          >
            {{ presentationError }}
          </p>
        </aside>

        <p
          v-if="terminalNotice"
          role="status"
          class="border-b border-slate-200 px-4 py-2 text-xs font-bold dark:border-dark-700"
          :class="terminalNotice.kind === 'failure'
            ? 'bg-rose-50/[var(--ai-chat-surface-opacity,1)] text-rose-700 dark:bg-rose-500/[min(0.1,var(--ai-chat-surface-opacity,1))] dark:text-rose-200'
            : 'bg-emerald-50/[var(--ai-chat-surface-opacity,1)] text-emerald-700 dark:bg-emerald-500/[min(0.1,var(--ai-chat-surface-opacity,1))] dark:text-emerald-200'"
          data-testid="ai-work-terminal-notice"
        >
          {{ terminalNotice.text }}
        </p>

        <div class="min-h-0 flex-1 overflow-y-auto px-4 py-3">
          <AiChatPanel
            :messages="chatStore.messages"
            :busy="chatStore.isBusy"
            :error="panelError"
            :input="chatStore.input"
            :conversation-id="chatStore.chat?.id ?? ''"
            :history-version="chatStore.historyVersion"
            @update:input="chatStore.input = $event"
            @send="chatStore.sendMessage()"
            @stop="chatStore.stopStreaming()"
          />
        </div>
      </section>
    </div>

    <RouterLink
      :to="aiWorkRoute"
      aria-label="打开 AI 助手"
      :aria-expanded="chatStore.floatingOpen && !floatingDrag.dragging.value"
      aria-controls="ai-work-floating-panel"
      title="点击打开 AI 助手，拖拽调整位置"
      data-testid="ai-work-floating-toggle"
      class="flex size-14 shrink-0 touch-none select-none items-center justify-center rounded-full border border-white/60 bg-primary-600 text-white shadow-xl shadow-primary-950/25 transition-colors duration-200 hover:bg-primary-500 hover:shadow-2xl hover:shadow-primary-950/30 focus:outline-none focus-visible:ring-4 focus-visible:ring-primary-300 dark:border-primary-300/20 dark:bg-primary-500 dark:hover:bg-primary-400"
      :class="floatingDrag.dragging.value ? 'cursor-grabbing' : 'cursor-grab'"
      @pointerdown="floatingDrag.start"
      @lostpointercapture="floatingDrag.cancel"
      @click.capture="floatingDrag.handleClick"
      @dragstart.prevent
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
    </RouterLink>
  </div>
</template>

<style scoped>
.ai-floating-panel {
  /* 外框保留约 1/3 底色，内部底色更淡，避免嵌套叠加后遮住页面。 */
  --ai-chat-surface-opacity: 0.12;
  --ai-chat-user-color: theme('colors.slate.900');
}

.dark .ai-floating-panel {
  --ai-chat-user-color: white;
}

.ai-floating-panel :deep(.ai-chat-composer),
.ai-floating-panel :deep(.ai-chat-composer-surface) {
  background-color: transparent;
}

.ai-floating-panel :deep(.btn) {
  background-image: none;
  background-color: transparent;
  box-shadow: none;
}

.ai-floating-panel :deep(.btn-primary) {
  background-color: rgb(var(--color-primary-600) / 0.12);
  color: var(--ai-chat-user-color);
}

.ai-floating-panel :deep(.shadow-sm),
.ai-floating-panel :deep(.shadow-lg),
.ai-floating-panel :deep(.shadow-xl) {
  box-shadow: none;
}
</style>
