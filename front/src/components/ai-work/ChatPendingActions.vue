<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { PhCaretDown, PhCaretLeft, PhCaretRight, PhCheck, PhPencilSimpleLine } from '@phosphor-icons/vue'
import { useAiChatStore } from '@/stores'
import type { AiWorkUiMessagesResponse, PendingToolCall } from '@/types/aiWork'
import { describeAiTool } from '@/utils/aiToolLabels'
import NativeToolApprovals from './NativeToolApprovals.vue'

const chat = useAiChatStore()
const activeKey = ref('')
const collapsed = ref(false)
const knownKeys = ref(new Set<string>())
const drafts = ref<Record<string, string>>({})
const customOpen = ref<Record<string, boolean>>({})
const errors = ref<Record<string, string>>({})
const submitting = ref(new Set<string>())
const customTextareaRef = ref<HTMLTextAreaElement | null>(null)

const items = computed(() => {
  function rows(scope: string, calls: PendingToolCall[], reply?: AiWorkUiMessagesResponse) {
    return calls.filter(call => call.kind === 'approval' || call.kind === 'input').map(call => ({
      key: JSON.stringify([scope, call.tool_call_id]),
      scope,
      call,
      reply,
      title: call.title || call.summary.split('\n')[0] || describeAiTool(call.tool_name),
      tag: call.kind === 'input' ? '补充信息' : '操作确认',
    }))
  }
  return [
    ...rows(chat.activeConversationId ?? '', chat.pendingToolCalls),
    ...chat.parallelReplies.flatMap(reply => rows(reply.conversation_id, reply.pending_tool_calls ?? [], reply)),
  ].sort((a, b) => (a.call.requested_at ?? '').localeCompare(b.call.requested_at ?? ''))
})

type PendingItem = (typeof items.value)[number]

watch(items, rows => {
  const keys = new Set(rows.map(row => row.key))
  const hasNewItem = rows.some(row => !knownKeys.value.has(row.key))
  knownKeys.value = keys

  for (const key of Object.keys(drafts.value)) if (!keys.has(key)) delete drafts.value[key]
  for (const key of Object.keys(customOpen.value)) if (!keys.has(key)) delete customOpen.value[key]
  for (const key of Object.keys(errors.value)) if (!keys.has(key)) delete errors.value[key]

  if (!rows.length) {
    activeKey.value = ''
    collapsed.value = false
    return
  }
  if (!keys.has(activeKey.value)) {
    activeKey.value = rows[0]!.key
  }
  if (hasNewItem) {
    collapsed.value = false
  }
}, { immediate: true })

const activeIndex = computed(() => {
  const index = items.value.findIndex(item => item.key === activeKey.value)
  return index >= 0 ? index : 0
})

const activeItem = computed<PendingItem | undefined>(() => items.value[activeIndex.value])

const failedOtherItem = computed<PendingItem | undefined>(() => (
  items.value.find(item => item.key !== activeItem.value?.key && Boolean(errors.value[item.key]))
))

function selectStep(index: number) {
  const target = items.value[index]
  if (!target) return
  activeKey.value = target.key
  collapsed.value = false
}

function toggleCollapsed() {
  collapsed.value = !collapsed.value
}

function isOptionSelected(item: PendingItem, option: string): boolean {
  return !customOpen.value[item.key] && drafts.value[item.key] === option
}

function chooseOption(item: PendingItem, option: string) {
  if (submitting.value.has(item.key)) return
  drafts.value[item.key] = option
  customOpen.value[item.key] = false
}

async function openCustomInput(item: PendingItem) {
  if (submitting.value.has(item.key)) return
  if (item.call.options?.includes(drafts.value[item.key] ?? '')) {
    drafts.value[item.key] = ''
  }
  customOpen.value[item.key] = true
  await nextTick()
  customTextareaRef.value?.focus()
}

function closeCustomInput(item: PendingItem) {
  customOpen.value[item.key] = false
  if (!item.call.options?.includes(drafts.value[item.key] ?? '')) {
    drafts.value[item.key] = ''
  }
}

function canSubmitInput(item: PendingItem): boolean {
  return Boolean(drafts.value[item.key]?.trim()) && !submitting.value.has(item.key)
}

async function submit(item: PendingItem, send: () => Promise<void>) {
  if (submitting.value.has(item.key)) return
  submitting.value.add(item.key)
  delete errors.value[item.key]
  const nextPending = items.value.find(row => row.key !== item.key && !submitting.value.has(row.key))
  if (nextPending) {
    activeKey.value = nextPending.key
  }
  try {
    await send()
  } catch (cause) {
    if (items.value.some(row => row.key === item.key)) {
      errors.value[item.key] = cause instanceof Error ? cause.message : '提交失败，请重试'
    }
  } finally {
    submitting.value.delete(item.key)
  }
}

function answer(item: PendingItem) {
  const text = drafts.value[item.key]?.trim()
  if (text) return submit(item, () => chat.answerQuestion(item.scope, item.call.tool_call_id, text))
}

function decide(item: PendingItem | undefined, id: string, approved: boolean) {
  if (!item) return
  return submit(item, () => item.reply
    ? chat.respondToParallelApproval(item.reply, id, approved)
    : chat.respondToApproval(id, approved, approved ? undefined : '用户拒绝此操作'))
}

function onTextareaKeydown(event: KeyboardEvent, item: PendingItem) {
  if ((event.metaKey || event.ctrlKey) && event.key === 'Enter' && canSubmitInput(item)) {
    event.preventDefault()
    void answer(item)
  }
}
</script>

<template>
  <section
    v-if="activeItem"
    aria-label="等待你处理"
    data-testid="chat-pending-actions"
    class="mb-2 flex max-h-[45%] min-h-0 shrink-0 flex-col overflow-hidden rounded-xl border border-slate-200/90 bg-white shadow-sm dark:border-dark-700 dark:bg-dark-900"
  >
    <!-- 步进顶栏 -->
    <div
      class="flex shrink-0 items-center justify-between gap-2 bg-slate-50/90 px-3.5 py-2 dark:bg-dark-800/80"
      :class="{ 'border-b border-slate-200/80 dark:border-dark-700/80': !collapsed }"
      data-testid="chat-pending-header"
    >
      <button
        type="button"
        class="flex min-w-0 flex-1 items-center gap-2 text-left focus-visible:outline-primary-500"
        :aria-expanded="!collapsed"
        :title="activeItem.title"
        data-testid="chat-pending-toggle"
        @click="toggleCollapsed"
      >
        <span class="h-2 w-2 shrink-0 rounded-full bg-amber-500 dark:bg-amber-400" aria-hidden="true" />
        <span class="min-w-0 truncate text-xs font-semibold text-slate-900 dark:text-white">{{ activeItem.title }}</span>
        <span class="shrink-0 text-xs text-slate-400 dark:text-accent-400">· {{ activeItem.tag }}</span>
        <PhCaretDown
          :size="13"
          aria-hidden="true"
          class="shrink-0 text-slate-400 transition-transform dark:text-accent-400"
          :class="{ '-rotate-90': collapsed }"
        />
      </button>

      <div class="flex shrink-0 items-center gap-1 text-xs text-slate-500 dark:text-accent-300">
        <template v-if="items.length > 1">
          <button
            type="button"
            aria-label="上一项"
            data-testid="chat-pending-prev"
            :disabled="activeIndex === 0"
            class="flex h-6 w-6 items-center justify-center rounded-md border border-slate-200 bg-white text-slate-600 transition hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-40 dark:border-dark-600 dark:bg-dark-800 dark:text-accent-200 dark:hover:bg-dark-700"
            @click="selectStep(activeIndex - 1)"
          >
            <PhCaretLeft :size="12" aria-hidden="true" />
          </button>
          <span class="px-1.5 tabular-nums font-medium" data-testid="chat-pending-counter">
            {{ activeIndex + 1 }} / {{ items.length }}
          </span>
          <button
            type="button"
            aria-label="下一项"
            data-testid="chat-pending-next"
            :disabled="activeIndex === items.length - 1"
            class="flex h-6 w-6 items-center justify-center rounded-md border border-slate-200 bg-white text-slate-600 transition hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-40 dark:border-dark-600 dark:bg-dark-800 dark:text-accent-200 dark:hover:bg-dark-700"
            @click="selectStep(activeIndex + 1)"
          >
            <PhCaretRight :size="12" aria-hidden="true" />
          </button>
        </template>
        <span v-else class="tabular-nums text-slate-400 dark:text-accent-500" data-testid="chat-pending-counter">1 / 1</span>
      </div>
    </div>

    <!-- 主内容滚动区 -->
    <template v-if="!collapsed">
      <div
        class="min-h-0 flex-1 overflow-y-auto overscroll-contain px-3.5"
        :class="activeItem.call.kind === 'approval' ? 'pt-3' : 'py-3'"
        data-testid="chat-pending-scroll"
      >
        <p
          v-if="errors[activeItem.key]"
          role="alert"
          class="mb-2.5 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-200"
        >
          {{ errors[activeItem.key] }}
        </p>

        <div
          v-else-if="failedOtherItem"
          role="alert"
          class="mb-2.5 flex items-center justify-between gap-2 rounded-lg border border-rose-200 bg-rose-50 px-3 py-1.5 text-xs text-rose-700 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-200"
        >
          <span class="min-w-0 truncate">「{{ failedOtherItem.title }}」{{ errors[failedOtherItem.key] }}</span>
          <button
            type="button"
            class="shrink-0 font-semibold underline hover:no-underline"
            @click="activeKey = failedOtherItem.key"
          >
            查看
          </button>
        </div>

        <NativeToolApprovals
          v-if="activeItem.call.kind === 'approval'"
          :reply="activeItem.reply"
          :tool-call-id="activeItem.call.tool_call_id"
          :submitting="submitting.has(activeItem.key)"
          @decide="(id, approved) => decide(activeItem, id, approved)"
        />

        <form v-else class="space-y-2.5" @submit.prevent="answer(activeItem)">
          <p v-if="activeItem.call.question" class="whitespace-pre-wrap break-words text-sm leading-6 text-slate-700 dark:text-accent-100">
            {{ activeItem.call.question }}
          </p>

          <!-- 有选项：纵向编号选项行 + 按需展开自定义输入 -->
          <div v-if="activeItem.call.options?.length" class="space-y-1.5">
            <button
              v-for="(option, optIndex) in activeItem.call.options"
              :key="option"
              type="button"
              :aria-pressed="isOptionSelected(activeItem, option)"
              :disabled="submitting.has(activeItem.key)"
              data-testid="chat-pending-option"
              class="flex w-full items-center justify-between gap-2.5 rounded-lg border px-3 py-2 text-left text-xs transition-colors disabled:cursor-not-allowed disabled:opacity-50"
              :class="isOptionSelected(activeItem, option)
                ? 'border-primary-500/60 bg-primary-50/80 text-primary-800 dark:border-primary-500/50 dark:bg-primary-500/15 dark:text-primary-200'
                : 'border-slate-200 bg-slate-50/70 text-slate-800 hover:border-slate-300 hover:bg-slate-100/80 dark:border-dark-700 dark:bg-dark-800/70 dark:text-accent-100 dark:hover:border-dark-500 dark:hover:bg-dark-800'"
              @click="chooseOption(activeItem, option)"
            >
              <span class="flex min-w-0 items-center gap-2.5">
                <span
                  class="flex h-4 w-4 shrink-0 items-center justify-center rounded-full border text-[11px] font-semibold"
                  :class="isOptionSelected(activeItem, option)
                    ? 'border-primary-500 bg-primary-500 text-white'
                    : 'border-slate-300 text-slate-400 dark:border-dark-500 dark:text-accent-400'"
                  aria-hidden="true"
                >
                  <PhCheck v-if="isOptionSelected(activeItem, option)" :size="10" weight="bold" />
                  <template v-else>{{ optIndex + 1 }}</template>
                </span>
                <span class="min-w-0 break-words font-medium leading-5">{{ option }}</span>
              </span>
              <span
                v-if="isOptionSelected(activeItem, option)"
                class="shrink-0 text-[11px] font-medium text-primary-600 dark:text-primary-300"
              >已选</span>
            </button>

            <div
              v-if="customOpen[activeItem.key]"
              class="rounded-lg border border-primary-500/50 bg-white p-2 dark:border-primary-500/40 dark:bg-dark-800"
            >
              <div class="mb-1 flex items-center justify-between text-[11px] text-slate-400 dark:text-accent-400">
                <span>自定义回复</span>
                <button
                  type="button"
                  class="hover:text-slate-600 dark:hover:text-accent-200"
                  data-testid="chat-pending-custom-cancel"
                  @click="closeCustomInput(activeItem)"
                >
                  取消
                </button>
              </div>
              <textarea
                ref="customTextareaRef"
                v-model="drafts[activeItem.key]"
                :aria-label="activeItem.title"
                rows="2"
                maxlength="32000"
                placeholder="输入你的自定义回复…"
                :disabled="submitting.has(activeItem.key)"
                class="block w-full resize-y bg-transparent text-xs leading-5 text-slate-900 outline-none placeholder:text-slate-400 dark:text-white dark:placeholder:text-accent-400"
                @keydown="onTextareaKeydown($event, activeItem)"
              />
            </div>

            <button
              v-else
              type="button"
              :disabled="submitting.has(activeItem.key)"
              data-testid="chat-pending-custom-toggle"
              class="flex w-full items-center gap-2.5 rounded-lg border border-dashed border-slate-300 bg-transparent px-3 py-2 text-left text-xs text-slate-500 transition-colors hover:border-slate-400 hover:bg-slate-50 hover:text-slate-700 disabled:cursor-not-allowed disabled:opacity-50 dark:border-dark-600 dark:text-accent-300 dark:hover:border-dark-500 dark:hover:bg-dark-800/60 dark:hover:text-accent-100"
              @click="openCustomInput(activeItem)"
            >
              <PhPencilSimpleLine :size="14" class="shrink-0" aria-hidden="true" />
              <span>其他回复（手动输入）…</span>
            </button>
          </div>

          <!-- 无选项：直接展示简洁回复框 -->
          <textarea
            v-else
            v-model="drafts[activeItem.key]"
            :aria-label="activeItem.title"
            rows="2"
            maxlength="32000"
            placeholder="输入你的回复…"
            :disabled="submitting.has(activeItem.key)"
            class="block w-full resize-y rounded-lg border border-slate-200 bg-slate-50/70 px-3 py-2 text-sm leading-5 text-slate-900 outline-none transition focus:border-primary-400 focus:bg-white focus:ring-2 focus:ring-primary-200/50 dark:border-dark-600 dark:bg-dark-800 dark:text-white dark:placeholder:text-accent-400 dark:focus:border-primary-500/60 dark:focus:ring-primary-500/15"
            @keydown="onTextareaKeydown($event, activeItem)"
          />
        </form>
      </div>

      <!-- 提问态统一固定底栏（审批态底栏由 NativeToolApprovals 渲染并吸底） -->
      <div
        v-if="activeItem.call.kind === 'input'"
        class="flex shrink-0 items-center justify-between gap-2 border-t border-slate-200/80 bg-slate-50/90 px-3.5 py-2 dark:border-dark-700/80 dark:bg-dark-800/80"
        data-testid="chat-pending-footer"
      >
        <span class="min-w-0 truncate text-xs text-slate-400 dark:text-accent-400">
          {{ submitting.has(activeItem.key) ? '正在提交回复…' : activeItem.call.options?.length ? '选择一项或手动输入后提交' : '填写内容后点击确认回复' }}
        </span>
        <button
          type="button"
          class="btn btn-primary !min-h-8 shrink-0 !rounded-lg !px-3.5 !py-1.5 text-xs"
          :disabled="!canSubmitInput(activeItem)"
          data-testid="chat-pending-submit"
          @click="answer(activeItem)"
        >
          {{ submitting.has(activeItem.key) ? '提交中…' : '确认回复' }}
        </button>
      </div>
    </template>
  </section>
</template>
