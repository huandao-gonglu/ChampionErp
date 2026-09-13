<script setup lang="ts">
import { computed, ref } from 'vue'
import { useAiChatStore } from '@/stores/aiChat'
const chat = useAiChatStore()
const error = ref('')
const submitting = ref<string | null>(null)
const approvals = computed(() => chat.messages.flatMap(message => message.parts.flatMap(part => {
  if (!('state' in part) || part.state !== 'approval-requested' || !('approval' in part)) return []
  const callId = 'toolCallId' in part ? String(part.toolCallId) : ''
  const pending = chat.pendingToolCalls.find(call => call.tool_call_id === callId && call.kind === 'approval')
  return pending ? [{ id: part.approval.id, callId, name: pending.tool_name, summary: pending.summary, input: 'input' in part ? part.input : {} }] : []
})))
async function respond(id: string, approved: boolean) {
  submitting.value = id
  error.value = ''
  try { await chat.respondToApproval(id, approved, approved ? undefined : '用户拒绝此操作') }
  catch (cause) { error.value = cause instanceof Error ? cause.message : '审批未提交，请重试' }
  finally { submitting.value = null }
}
</script>
<template>
  <div v-if="approvals.length" class="space-y-3 pb-3" data-testid="native-tool-approvals">
    <section v-for="approval in approvals" :key="approval.callId" class="rounded-xl border border-amber-200 bg-amber-50 p-4 dark:border-amber-700 dark:bg-amber-950">
      <p class="font-semibold">等待确认：{{ approval.name }}</p>
      <p class="mt-2 text-sm">{{ approval.summary }}</p>
      <details class="mt-2 text-xs"><summary>查看操作参数</summary><pre class="overflow-auto whitespace-pre-wrap">{{ JSON.stringify(approval.input, null, 2) }}</pre></details>
      <div class="mt-3 flex gap-2">
        <button class="btn btn-primary" :disabled="submitting !== null" @click="respond(approval.id, true)">批准</button>
        <button class="btn btn-outline" :disabled="submitting !== null" @click="respond(approval.id, false)">拒绝</button>
      </div>
    </section>
    <p v-if="error" role="alert" class="text-sm text-rose-600">{{ error }}</p>
  </div>
</template>
