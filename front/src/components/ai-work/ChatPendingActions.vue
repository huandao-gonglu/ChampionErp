<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { PhCaretDown } from '@phosphor-icons/vue'
import { useAiChatStore } from '@/stores'
import type { AiWorkUiMessagesResponse, PendingToolCall } from '@/types/aiWork'
import { describeAiTool } from '@/utils/aiToolLabels'
import NativeToolApprovals from './NativeToolApprovals.vue'

const chat = useAiChatStore()
const expanded = ref('')
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
watch(items, rows => {
  const keys = new Set(rows.map(row => row.key))
  if (!keys.has(expanded.value)) expanded.value = ''
  for (const key of Object.keys(drafts.value)) if (!keys.has(key)) delete drafts.value[key]
  for (const key of Object.keys(errors.value)) if (!keys.has(key)) delete errors.value[key]
})
type PendingItem = (typeof items.value)[number]

async function submit(item: PendingItem, send: () => Promise<void>) {
  if (submitting.value.has(item.key)) return
  submitting.value.add(item.key)
  expanded.value = ''
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
  <section v-if="items.length" aria-label="等待你处理" class="mb-1 max-h-[45%] shrink-0 overflow-y-auto overscroll-contain rounded-xl border border-slate-300 dark:border-dark-600">
    <div v-for="item in items" :key="item.key" class="border-b border-slate-200 last:border-b-0 dark:border-dark-600">
      <button type="button" :aria-expanded="expanded === item.key" :disabled="submitting.has(item.key)" :title="item.title"
        class="flex w-full items-center gap-2 px-3 py-2 text-left text-sm font-semibold focus-visible:outline-primary-500"
        @click="expanded = expanded === item.key ? '' : item.key">
        <PhCaretDown :size="14" aria-hidden="true" class="shrink-0" :class="{ '-rotate-90': expanded !== item.key }" />
        <span class="truncate">{{ item.title }}</span>
      </button>
      <p v-if="errors[item.key]" role="alert" class="px-3 pb-2 text-sm text-rose-600">{{ errors[item.key] }}</p>
      <div v-if="expanded === item.key" class="px-3 pb-3">
        <NativeToolApprovals v-if="item.call.kind === 'approval'" :reply="item.reply" :tool-call-id="item.call.tool_call_id"
          @decide="(id, approved) => decide(item, id, approved)" />
        <form v-else class="space-y-2" @submit.prevent="answer(item)">
          <p class="whitespace-pre-wrap break-words text-sm">{{ item.call.question }}</p>
          <div v-if="item.call.options?.length" class="flex flex-wrap gap-2">
            <button v-for="option in item.call.options" :key="option" type="button" class="btn btn-outline"
              :aria-pressed="drafts[item.key] === option" :disabled="submitting.has(item.key)" @click="drafts[item.key] = option">{{ option }}</button>
          </div>
          <textarea v-model="drafts[item.key]" :aria-label="item.title" rows="2" maxlength="32000" placeholder="填写回复"
            :disabled="submitting.has(item.key)" class="block w-full resize-y rounded-lg border border-slate-300 bg-transparent p-2 text-sm dark:border-dark-600" />
          <button type="submit" class="btn btn-primary" :disabled="!drafts[item.key]?.trim() || submitting.has(item.key)">提交回复</button>
        </form>
      </div>
    </div>
  </section>
</template>
