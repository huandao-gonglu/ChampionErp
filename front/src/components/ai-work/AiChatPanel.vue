<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { PhArrowDown } from '@phosphor-icons/vue'
import type { UIMessage } from 'ai'
import AiChatComposer from './AiChatComposer.vue'
import AiMessageList from './AiMessageList.vue'
import NativeToolApprovals from './NativeToolApprovals.vue'
import { useAiChatStore } from '@/stores'

const props = withDefaults(defineProps<{
  messages: UIMessage[]
  busy: boolean
  error?: string
  input: string
  /** 当前活动 conversation；切换、首次进入与 history version 变化时探测任务关联。 */
  conversationId?: string
  historyVersion?: number
}>(), {
  error: '',
  conversationId: '',
  historyVersion: 0,
})

const emit = defineEmits<{
  (event: 'update:input', value: string): void
  (event: 'send'): void
  (event: 'stop'): void
}>()

const scrollRef = ref<HTMLElement | null>(null)
const contentRef = ref<HTMLElement | null>(null)
const followingLatest = ref(true)
const showJumpToLatest = ref(false)
const chatStore = useAiChatStore()
let lastScrollTop = 0
let lastTouchY: number | undefined
let resizeObserver: ResizeObserver | undefined

function updateScrollPosition() {
  const element = scrollRef.value
  if (!element) return true
  // 只容忍像素取整误差，向上查看少量内容也应暂停跟随。
  const atBottom = element.scrollHeight - element.scrollTop - element.clientHeight <= 2
  showJumpToLatest.value = !atBottom
  lastScrollTop = element.scrollTop
  return atBottom
}

function handleScroll() {
  const movingUp = (scrollRef.value?.scrollTop ?? 0) < lastScrollTop - 1
  if (updateScrollPosition()) {
    followingLatest.value = true
  } else if (movingUp) {
    followingLatest.value = false
  }
}

function handleWheel(event: WheelEvent) {
  // 在浏览器实际滚动前暂停，避免同一帧的流式更新抢先拉回底部。
  if (event.deltaY < 0) followingLatest.value = false
}

function handleTouchStart(event: TouchEvent) {
  lastTouchY = event.touches[0]?.clientY
}

function handleTouchMove(event: TouchEvent) {
  const touchY = event.touches[0]?.clientY
  if (touchY !== undefined && lastTouchY !== undefined && touchY > lastTouchY) {
    followingLatest.value = false
  }
  lastTouchY = touchY
}

function handleKeydown(event: KeyboardEvent) {
  if (event.target !== event.currentTarget) return
  if (['ArrowUp', 'PageUp', 'Home'].includes(event.key) || (event.key === ' ' && event.shiftKey)) {
    followingLatest.value = false
  }
}

async function scrollToBottom() {
  await nextTick()
  const element = scrollRef.value
  // 等待渲染期间用户仍可能开始查看历史，执行前必须再次检查。
  if (element && followingLatest.value) {
    element.scrollTop = element.scrollHeight
  }
  updateScrollPosition()
}

function jumpToLatest() {
  followingLatest.value = true
  void scrollToBottom()
}

watch(() => [props.messages, props.busy, props.error], () => {
  void scrollToBottom()
}, { deep: true })

watch(() => props.conversationId, jumpToLatest)

onMounted(() => {
  void scrollToBottom()
  // 图片加载、工具卡片展开和输入框高度变化也会改变滚动边界。
  resizeObserver = new ResizeObserver(() => { void scrollToBottom() })
  if (scrollRef.value) resizeObserver.observe(scrollRef.value)
  if (contentRef.value) resizeObserver.observe(contentRef.value)
})

onBeforeUnmount(() => resizeObserver?.disconnect())
</script>

<template>
  <section class="mx-auto flex h-full w-full max-w-5xl flex-col" data-testid="ai-chat-panel">
    <div class="relative min-h-0 flex-1">
      <div
        ref="scrollRef"
        class="h-full overflow-y-auto"
        tabindex="0"
        aria-label="对话消息"
        data-testid="ai-chat-scroll"
        @scroll="handleScroll"
        @wheel.passive="handleWheel"
        @touchstart.passive="handleTouchStart"
        @touchmove.passive="handleTouchMove"
        @keydown="handleKeydown"
      >
        <div ref="contentRef" class="space-y-4 pb-5">
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
              可以查询业务事实，也可以让全局 Agent 准备草稿或执行商品操作；审批方式由输入框中的权限选择决定。
            </p>
          </div>

          <!-- 消息气泡 -->
          <AiMessageList v-else :messages="messages" :pending-tool-calls="chatStore.pendingToolCalls" />

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
      </div>
      <button
        v-if="showJumpToLatest"
        type="button"
        class="absolute bottom-3 left-1/2 z-10 flex h-9 w-9 -translate-x-1/2 items-center justify-center rounded-full border border-slate-300 bg-white text-slate-700 shadow-md transition-colors hover:bg-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 dark:border-dark-600 dark:bg-dark-800 dark:text-accent-100 dark:hover:bg-dark-700"
        aria-label="跳转到最新消息"
        title="跳转到最新消息"
        data-testid="ai-chat-jump-to-latest"
        @click="jumpToLatest"
      >
        <PhArrowDown :size="20" aria-hidden="true" />
      </button>
    </div>

    <NativeToolApprovals />
    <p v-if="chatStore.pendingToolCalls.some(call => call.kind === 'external')" class="mb-2 text-sm text-slate-500">后台操作执行中，Agent 等待工具结果。可以继续补充资料或纠正要求。</p>
    <div v-if="chatStore.receivedNotice" role="status" class="mb-2 rounded-lg bg-sky-50 p-3 text-sm text-sky-800">
      {{ chatStore.receivedNotice }}
      <p v-for="message in chatStore.receivedMessages" :key="message.message_id">{{ message.text }}</p>
    </div>

    <!-- 输入框 -->
    <AiChatComposer
      :model-value="input"
      :busy="busy || chatStore.canStop"
      :stopping="chatStore.stopping"
      :conversation-id="conversationId"
      @update:model-value="emit('update:input', $event)"
      @send="emit('send')"
      @stop="emit('stop')"
    />
  </section>
</template>
