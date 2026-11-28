<script setup lang="ts">
import { computed } from 'vue'
import { useAiChatStore } from '@/stores/aiChat'
import PackageEstimateReview from './PackageEstimateReview.vue'
import type { AiWorkUiMessagesResponse } from '@/types/aiWork'

const props = defineProps<{
  reply?: AiWorkUiMessagesResponse
  toolCallId: string
  submitting?: boolean
}>()
const emit = defineEmits<{ decide: [id: string, approved: boolean] }>()
const chat = useAiChatStore()

function ruleChange(input: unknown) {
  if (!input || typeof input !== 'object' || !('text' in input) || typeof input.text !== 'string') return null
  const current = 'current_text' in input && typeof input.current_text === 'string' ? input.current_text : null
  return { current, text: input.text }
}

const approvals = computed(() => (props.reply?.messages ?? chat.messages).flatMap(message => message.parts.flatMap(part => {
  if (!('state' in part) || part.state !== 'approval-requested' || !('approval' in part)) return []
  const callId = 'toolCallId' in part ? String(part.toolCallId) : ''
  if (callId !== props.toolCallId) return []
  const pending = (props.reply?.pending_tool_calls ?? chat.pendingToolCalls).find(call => call.tool_call_id === callId && call.kind === 'approval')
  if (!pending) return []
  const input = 'input' in part ? part.input : {}
  const rule = pending.tool_name === 'user_agents_update' ? ruleChange(input) : null
  return [{ id: part.approval.id, callId, tool: pending.tool_name, summary: pending.summary, input, rule }]
})))

const actionDisabled = computed(() => Boolean(props.submitting) || (!props.reply && chat.isBusy))
</script>

<template>
  <div v-if="approvals.length" class="space-y-3" data-testid="native-tool-approvals">
    <section v-for="approval in approvals" :key="approval.callId" class="space-y-3">
      <div class="space-y-2.5 rounded-lg border border-slate-200/80 bg-slate-50/70 p-3 dark:border-dark-700/80 dark:bg-dark-800/60">
        <p class="whitespace-pre-wrap break-words text-sm leading-6 text-slate-800 dark:text-accent-100">
          {{ approval.summary }}
        </p>

        <PackageEstimateReview v-if="approval.tool === 'draft_package_estimates_confirm'" :input="approval.input" />

        <dl v-if="approval.rule" class="space-y-2 border-t border-slate-200/80 pt-2 text-xs dark:border-dark-700">
          <template v-if="approval.rule.current">
            <dt class="font-semibold text-slate-500 dark:text-accent-400">原规则</dt>
            <dd class="whitespace-pre-wrap break-words rounded-md bg-white px-2.5 py-1.5 text-slate-600 dark:bg-dark-900/70 dark:text-accent-200">
              {{ approval.rule.current }}
            </dd>
          </template>
          <dt class="font-semibold text-slate-500 dark:text-accent-400">要保存的规则</dt>
          <dd class="whitespace-pre-wrap break-words rounded-md bg-white px-2.5 py-1.5 text-slate-900 dark:bg-dark-900/70 dark:text-white">
            {{ approval.rule.text }}
          </dd>
        </dl>

        <details v-else class="border-t border-slate-200/80 pt-2 text-xs dark:border-dark-700">
          <summary class="cursor-pointer select-none py-0.5 text-slate-500 transition-colors hover:text-slate-700 dark:text-accent-400 dark:hover:text-accent-200">
            展开调用参数
          </summary>
          <pre class="mt-2 max-h-48 overflow-auto whitespace-pre-wrap break-all rounded-md bg-white p-2.5 font-mono text-[11px] leading-5 text-slate-600 dark:bg-dark-900 dark:text-accent-200">{{ JSON.stringify(approval.input, null, 2) }}</pre>
        </details>
      </div>

      <div
        class="sticky bottom-0 z-10 -mx-3.5 flex flex-wrap items-center justify-between gap-2 border-t border-slate-200/80 bg-slate-50/95 px-3.5 py-2 backdrop-blur-sm dark:border-dark-700/80 dark:bg-dark-800/95"
        data-testid="native-tool-approval-actions"
      >
        <span class="min-w-0 truncate text-xs text-slate-400 dark:text-accent-400">
          {{ submitting ? '正在提交审批结果…' : '核对无误后点击批准' }}
        </span>
        <div class="flex shrink-0 items-center gap-2">
          <button
            type="button"
            class="btn btn-outline !min-h-8 !rounded-lg !px-3 !py-1.5 text-xs"
            :disabled="actionDisabled"
            @click="emit('decide', approval.id, false)"
          >
            拒绝
          </button>
          <button
            type="button"
            class="btn btn-primary !min-h-8 !rounded-lg !px-3.5 !py-1.5 text-xs"
            :disabled="actionDisabled"
            @click="emit('decide', approval.id, true)"
          >
            批准
          </button>
        </div>
      </div>
    </section>
  </div>
</template>
