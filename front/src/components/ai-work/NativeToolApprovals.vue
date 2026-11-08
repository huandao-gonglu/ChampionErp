<script setup lang="ts">
import { computed, ref } from 'vue'
import { useAiChatStore } from '@/stores/aiChat'
import { describeAiTool } from '@/utils/aiToolLabels'
import PackageEstimateReview from './PackageEstimateReview.vue'
import type { AiWorkUiMessagesResponse } from '@/types/aiWork'
const props = defineProps<{ reply?: AiWorkUiMessagesResponse; toolCallId?: string; busy?: boolean }>()
const emit = defineEmits<{ submitting: [value: boolean] }>()
const chat = useAiChatStore()
const error = ref('')
const submitting = ref<string | null>(null)
const approvals = computed(() => (props.reply?.messages ?? chat.messages).flatMap(message => message.parts.flatMap(part => {
  if (!('state' in part) || part.state !== 'approval-requested' || !('approval' in part)) return []
  const callId = 'toolCallId' in part ? String(part.toolCallId) : ''
  if (props.toolCallId && callId !== props.toolCallId) return []
  const pending = (props.reply?.pending_tool_calls ?? chat.pendingToolCalls).find(call => call.tool_call_id === callId && call.kind === 'approval')
  return pending ? [{ id: part.approval.id, callId, name: describeAiTool(pending.tool_name), tool: pending.tool_name, summary: pending.summary, input: 'input' in part ? part.input : {} }] : []
})))
async function respond(id: string, approved: boolean) {
  if (props.busy || submitting.value !== null || (!props.reply && chat.isBusy)) return
  submitting.value = id
  emit('submitting', true)
  error.value = ''
  try {
    if (props.reply) await chat.respondToParallelApproval(props.reply, id, approved)
    else await chat.respondToApproval(id, approved, approved ? undefined : '用户拒绝此操作')
  }
  catch (cause) { error.value = cause instanceof Error ? cause.message : '审批未提交，请重试' }
  finally { submitting.value = null; emit('submitting', false) }
}
</script>
<template>
  <div v-if="approvals.length" class="mt-2 space-y-3" data-testid="native-tool-approvals">
    <section v-for="approval in approvals" :key="approval.callId" class="rounded-xl border border-amber-200 bg-amber-50/[var(--ai-chat-surface-opacity,1)] p-4 dark:border-amber-700 dark:bg-amber-950/[var(--ai-chat-surface-opacity,1)]">
      <p v-if="!toolCallId" class="font-semibold">等待确认：{{ approval.name }}</p>
      <p class="mt-2 text-sm">{{ approval.summary }}</p>
      <PackageEstimateReview v-if="approval.tool === 'draft_package_estimates_confirm'" :input="approval.input" />
      <details class="mt-2 text-xs"><summary>查看操作参数</summary><pre class="overflow-auto whitespace-pre-wrap">{{ JSON.stringify(approval.input, null, 2) }}</pre></details>
      <div class="mt-3 flex gap-2">
        <button class="btn btn-primary" :disabled="busy || submitting !== null || (!reply && chat.isBusy)" @click="respond(approval.id, true)">批准</button>
        <button class="btn btn-outline" :disabled="busy || submitting !== null || (!reply && chat.isBusy)" @click="respond(approval.id, false)">拒绝</button>
      </div>
    </section>
    <p v-if="error" role="alert" class="text-sm text-rose-600">{{ error }}</p>
  </div>
</template>
