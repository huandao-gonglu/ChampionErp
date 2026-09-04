<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { PhPushPin } from '@phosphor-icons/vue'
import AiChatPanel from '@/components/ai-work/AiChatPanel.vue'
import AiMessageList from '@/components/ai-work/AiMessageList.vue'
import { workflowNavItems } from '@/constants/navigation'
import { useAiChatStore, useAiWorkDisplayStore } from '@/stores'

const route = useRoute()
const chatStore = useAiChatStore()
const displayStore = useAiWorkDisplayStore()
const floatingElement = ref<HTMLElement | null>(null)
const pointerWithin = ref(false)
const focusWithin = ref(false)
const isPinned = ref(false)
let noticeTimer: ReturnType<typeof setTimeout> | null = null

// AiWork 页面内置完整对话区域，浮动入口只在其他页面显示。
const shouldRender = computed(() => route.name !== 'AiWork')

const displayMode = computed(() => displayStore.displayMode)
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

// 前台 presentation 期间进入 AiWork 时携带 presentation conversation 与
// presentation_id，页面按同一 observe Chat 继续展示；无 presentation 时恢复
// global.chat 目标。
const aiWorkRoute = computed(() => {
  if (foregroundPresentation.value) {
    return {
      name: 'AiWork',
      query: {
        ...workspaceReturnQuery.value,
        conversation_id: foregroundPresentation.value.conversationId,
        presentation_id: foregroundPresentation.value.presentationId,
      },
    }
  }
  return {
    name: 'AiWork',
    query: {
      ...workspaceReturnQuery.value,
      ...(chatStore.activeConversationId
        ? { conversation_id: chatStore.activeConversationId }
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

// terminal 提示只短暂展示，不能永久停留在业务模式或遮挡 global.chat。
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
      :aria-label="displayMode === 'presentation' ? '前台 AI 任务只读展示' : '全局 AI 浮动对话'"
      data-testid="ai-work-floating-panel"
      class="flex w-[400px] max-w-[calc(100vw-2.5rem)] flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white text-slate-900 shadow-2xl shadow-slate-950/20 dark:border-dark-700 dark:bg-dark-900 dark:text-white"
    >
      <!-- 前台 presentation：只读展示，隐藏 composer；不覆盖 global.chat -->
      <template v-if="displayMode === 'presentation' && foregroundPresentation">
        <header class="flex items-center justify-between gap-3 border-b border-slate-200 px-4 py-3 dark:border-dark-700">
          <div class="min-w-0">
            <p class="text-xs font-black uppercase tracking-[0.14em] text-primary-600 dark:text-primary-300">
              {{ foregroundPresentation.displayTitle || '前台 AI 任务' }}
            </p>
            <p class="mt-0.5 truncate text-[11px] text-slate-500 dark:text-accent-300">
              {{ foregroundPresentation.conversationId }} · {{ displayStore.presentationStatusText }}
            </p>
          </div>
          <div class="flex shrink-0 items-center">
            <button
              type="button"
              class="flex size-8 items-center justify-center rounded-full transition hover:bg-slate-100 dark:hover:bg-dark-800"
              :class="isPinned
                ? 'bg-primary-50 text-primary-600 dark:bg-primary-500/15 dark:text-primary-300'
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
              class="flex size-8 items-center justify-center rounded-full text-lg text-slate-400 transition hover:bg-slate-100 hover:text-slate-700 dark:hover:bg-dark-800 dark:hover:text-white"
              aria-label="收起浮动对话"
              data-testid="ai-work-floating-close"
              @click="chatStore.closeFloating()"
            >
              ×
            </button>
          </div>
        </header>

        <div
          class="h-[480px] max-h-[calc(100vh-180px)] space-y-3 overflow-y-auto px-4 py-3"
          data-testid="ai-work-presentation-run"
        >
          <AiMessageList
            v-if="displayStore.presentationMessages.length"
            :messages="displayStore.presentationMessages"
          />
          <!-- 已有错误（如展示连接中断的降级等待）时不显示“等待首个事件”空状态，避免语义矛盾 -->
          <p
            v-else-if="!presentationError"
            class="rounded-2xl border border-dashed border-primary-300 bg-primary-50/60 px-6 py-10 text-center text-sm font-bold text-slate-500 dark:border-primary-500/40 dark:bg-primary-500/10 dark:text-accent-300"
            data-testid="ai-work-presentation-empty"
          >
            AI Agent 已启动，等待首个事件…
          </p>
          <p
            v-if="displayStore.presentationBusy"
            class="text-xs text-slate-400"
            data-testid="ai-work-presentation-streaming"
          >
            AI Agent 正在运行（只读展示）…
          </p>
          <p
            v-if="presentationError"
            role="alert"
            class="rounded-xl bg-rose-50 p-3 text-sm text-rose-700 ring-1 ring-rose-200 dark:bg-rose-500/10 dark:text-rose-200 dark:ring-rose-500/30"
            data-testid="ai-work-presentation-error"
          >
            {{ presentationError }}
          </p>
        </div>
      </template>

      <!-- global.chat：现有可输入面板 + 短暂 terminal 提示 -->
      <template v-else>
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
              class="flex size-8 items-center justify-center rounded-full transition hover:bg-slate-100 dark:hover:bg-dark-800"
              :class="isPinned
                ? 'bg-primary-50 text-primary-600 dark:bg-primary-500/15 dark:text-primary-300'
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
              class="flex size-8 items-center justify-center rounded-full text-lg text-slate-400 transition hover:bg-slate-100 hover:text-slate-700 dark:hover:bg-dark-800 dark:hover:text-white"
              aria-label="收起浮动对话"
              data-testid="ai-work-floating-close"
              @click="chatStore.closeFloating()"
            >
              ×
            </button>
          </div>
        </header>

        <p
          v-if="terminalNotice"
          role="status"
          class="border-b border-slate-200 px-4 py-2 text-xs font-bold dark:border-dark-700"
          :class="terminalNotice.kind === 'failure'
            ? 'bg-rose-50 text-rose-700 dark:bg-rose-500/10 dark:text-rose-200'
            : 'bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-200'"
          data-testid="ai-work-terminal-notice"
        >
          {{ terminalNotice.text }}
        </p>

        <div class="h-[480px] max-h-[calc(100vh-180px)] px-4 py-3">
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
      </template>
    </section>

    <RouterLink
      :to="aiWorkRoute"
      aria-label="打开 AI Work"
      :aria-expanded="chatStore.floatingOpen"
      aria-controls="ai-work-floating-panel"
      title="打开 AI Work"
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
    </RouterLink>
  </div>
</template>
