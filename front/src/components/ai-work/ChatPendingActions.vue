<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { PhCaretDown } from '@phosphor-icons/vue'
import { useAiChatStore } from '@/stores'
import type { AiWorkUiMessagesResponse, PendingToolCall } from '@/types/aiWork'
import { describeAiTool } from '@/utils/aiToolLabels'
import NativeToolApprovals from './NativeToolApprovals.vue'

const chat = useAiChatStore()
const expanded = ref('')
const userCollapsed = ref(new Set<string>())
const drafts = ref<Record<string, string>>({})
const errors = ref<Record<string, string>>({})
const submitting = ref(new Set<string>())
const items = computed(() => {
  function rows(scope: string, calls: PendingToolCall[], reply?: AiWorkUiMessagesResponse) {
    return calls.filter(call => call.kind === 'approval' || call.kind === 'input').map(call => ({
      key: JSON.stringify([scope, call.tool_call_id]), scope, call, reply,
      title: call.title || call.summary.split('\n')[0] || describeAiTool(call.tool_name),
    }))
  }
  return [
    ...rows(chat.activeConversationId ?? '', chat.pendingToolCalls),
    ...chat.parallelReplies.flatMap(reply => rows(reply.conversation_id, reply.pending_tool_calls ?? [], reply)),
  ].sort((a, b) => (a.call.requested_at ?? '').localeCompare(b.call.requested_at ?? ''))
})
const pendingHeaderLabel = computed(() => {
  const inputs = items.value.filter(item => item.call.kind === 'input').length
  const approvals = items.value.length - inputs
  if (inputs && !approvals) return inputs === 1 ? 'AI 正在等待你回复' : `AI 正在等待你回复（${inputs} 项）`
  if (approvals && !inputs) return approvals === 1 ? '等待你确认操作' : `等待你确认操作（${approvals} 项）`
  return `等待你处理（${items.value.length} 项）`
})
watch(items, rows => {
  const keys = new Set(rows.map(row => row.key))
  if (!keys.has(expanded.value)) expanded.value = ''
  for (const key of userCollapsed.value) if (!keys.has(key)) userCollapsed.value.delete(key)
  for (const key of Object.keys(drafts.value)) if (!keys.has(key)) delete drafts.value[key]
  for (const key of Object.keys(errors.value)) if (!keys.has(key)) delete errors.value[key]
  // 只有单条待回复/待确认时自动展开，避免完整提问藏在折叠栏里被忽略。
  if (rows.length === 1 && !expanded.value && !submitting.value.has(rows[0].key) && !userCollapsed.value.has(rows[0].key)) {
    expanded.value = rows[0].key
  }
}, { immediate: true })
type PendingItem = (typeof items.value)[number]

function toggleExpanded(key: string) {
  if (expanded.value === key) {
    expanded.value = ''
    userCollapsed.value.add(key)
  } else {
    expanded.value = key
    userCollapsed.value.delete(key)
  }
}

async function submit(item: PendingItem, send: () => Promise<void>) {
  if (submitting.value.has(item.key)) return
  submitting.value.add(item.key)
  expanded.value = ''
  userCollapsed.value.add(item.key)
  delete errors.value[item.key]
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
function decide(item: PendingItem, id: string, approved: boolean) {
  return submit(item, () => item.reply
    ? chat.respondToParallelApproval(item.reply, id, approved)
    : chat.respondToApproval(id, approved, approved ? undefined : '用户拒绝此操作'))
}
</script>

<template>
  <section
    v-if="items.length"
    aria-label="等待你处理"
    data-testid="chat-pending-actions"
    class="max-h-[45%] shrink-0 overflow-y-auto overscroll-contain border-t-2 border-amber-400 bg-amber-50/70 px-3 pt-2 dark:border-amber-500/60 dark:bg-amber-500/10"
  >
    <div class="mb-1 flex items-center gap-2 text-xs font-bold text-amber-900 dark:text-amber-200" data-testid="chat-pending-header">
      <span class="inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-rose-500 px-1 text-[10px] font-bold leading-none text-white" aria-hidden="true">{{ items.length }}</span>
      <span>{{ pendingHeaderLabel }}</span>
    </div>
    <div v-for="item in items" :key="item.key" class="border-b border-amber-200/80 last:border-b-0 dark:border-amber-500/20">
      <button
        type="button" :aria-expanded="expanded === item.key" :disabled="submitting.has(item.key)" :title="item.title"
        class="flex w-full items-center gap-2 py-2 text-left text-sm font-semibold text-slate-900 focus-visible:outline-primary-500 dark:text-white"
        @click="toggleExpanded(item.key)"
      >
        <PhCaretDown :size="14" aria-hidden="true" class="shrink-0 text-amber-700 dark:text-amber-300" :class="{ '-rotate-90': expanded !== item.key }" />
        <span class="min-w-0 break-words" :class="{ truncate: expanded !== item.key }">{{ item.title }}</span>
      </button>
      <p v-if="errors[item.key]" role="alert" class="pb-2 text-sm text-rose-600">{{ errors[item.key] }}</p>
      <div v-if="expanded === item.key" class="min-w-0 pb-3">
        <NativeToolApprovals
          v-if="item.call.kind === 'approval'" :reply="item.reply" :tool-call-id="item.call.tool_call_id"
          @decide="(id, approved) => decide(item, id, approved)"
        />
        <form v-else class="space-y-2" @submit.prevent="answer(item)">
          <p class="whitespace-pre-wrap break-words text-sm text-slate-800 dark:text-accent-100">{{ item.call.question }}</p>
          <div v-if="item.call.options?.length" class="flex flex-wrap gap-2">
            <button
              v-for="option in item.call.options" :key="option" type="button" class="btn btn-outline"
              :aria-pressed="drafts[item.key] === option" :disabled="submitting.has(item.key)" @click="drafts[item.key] = option"
            >
              {{ option }}
            </button>
          </div>
          <textarea
            v-model="drafts[item.key]" :aria-label="item.title" rows="2" maxlength="32000" placeholder="填写回复"
            :disabled="submitting.has(item.key)" class="block w-full resize-y rounded-lg border border-slate-300 bg-white p-2 text-sm dark:border-dark-600 dark:bg-dark-800"
          />
          <button type="submit" class="btn btn-primary" :disabled="!drafts[item.key]?.trim() || submitting.has(item.key)">提交回复</button>
        </form>
      </div>
    </div>
  </section>
</template>
