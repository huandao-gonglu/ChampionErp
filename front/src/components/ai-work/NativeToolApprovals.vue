<script setup lang="ts">
import { computed } from 'vue'
import { useAiChatStore } from '@/stores/aiChat'
import PackageEstimateReview from './PackageEstimateReview.vue'
import type { AiWorkUiMessagesResponse } from '@/types/aiWork'
const props = defineProps<{ reply?: AiWorkUiMessagesResponse; toolCallId: string }>()
const emit = defineEmits<{ decide: [id: string, approved: boolean] }>()
const chat = useAiChatStore()
const approvals = computed(() => (props.reply?.messages ?? chat.messages).flatMap(message => message.parts.flatMap(part => {
  if (!('state' in part) || part.state !== 'approval-requested' || !('approval' in part)) return []
  const callId = 'toolCallId' in part ? String(part.toolCallId) : ''
  if (callId !== props.toolCallId) return []
  const pending = (props.reply?.pending_tool_calls ?? chat.pendingToolCalls).find(call => call.tool_call_id === callId && call.kind === 'approval')
  return pending ? [{ id: part.approval.id, callId, tool: pending.tool_name, summary: pending.summary, input: 'input' in part ? part.input : {} }] : []
})))
</script>
<template>
  <div v-if="approvals.length" class="mt-2 space-y-3" data-testid="native-tool-approvals">
    <section v-for="approval in approvals" :key="approval.callId" class="rounded-xl border border-amber-200 bg-amber-50/[var(--ai-chat-surface-opacity,1)] p-4 dark:border-amber-700 dark:bg-amber-950/[var(--ai-chat-surface-opacity,1)]">
      <p class="mt-2 text-sm">{{ approval.summary }}</p>
      <PackageEstimateReview v-if="approval.tool === 'draft_package_estimates_confirm'" :input="approval.input" />
      <details class="mt-2 text-xs"><summary>查看操作参数</summary><pre class="overflow-auto whitespace-pre-wrap">{{ JSON.stringify(approval.input, null, 2) }}</pre></details>
      <div class="mt-3 flex gap-2">
        <button class="btn btn-primary" :disabled="!reply && chat.isBusy" @click="emit('decide', approval.id, true)">批准</button>
        <button class="btn btn-outline" :disabled="!reply && chat.isBusy" @click="emit('decide', approval.id, false)">拒绝</button>
      </div>
    </section>
  </div>
</template>
