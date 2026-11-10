<script setup lang="ts">
import { computed } from 'vue'
import type { UIMessage } from 'ai'
import AiMessagePart from './AiMessagePart.vue'
import AiAssistantTurn from './AiAssistantTurn.vue'
import type { PendingToolCall } from '@/types/aiWork'
import { groupAiMessages } from '@/utils/aiMessagePresentation'

const props = defineProps<{
  messages: UIMessage[]
  pendingToolCalls?: PendingToolCall[]
  active?: boolean
  allowBranch?: boolean
}>()
const emit = defineEmits<{ branch: [messageId: string] }>()
const groups = computed(() => groupAiMessages(props.messages))
</script>

<template>
  <div class="space-y-4" data-testid="ai-message-list">
    <article
      v-for="(message, groupIndex) in groups"
      :key="message.id"
      class="flex"
      :class="message.role === 'user' ? 'justify-end' : 'justify-start'"
      :data-testid="`ai-message-${message.role}`"
    >
      <div
        class="min-w-0 text-sm leading-6"
        :class="message.role === 'user'
          ? 'max-w-[82%] rounded-2xl rounded-br-md bg-primary-600 px-4 py-3 text-white shadow-sm'
          : 'w-full py-2 text-slate-800 dark:text-accent-100'"
      >
        <AiAssistantTurn
          v-if="message.role === 'assistant'"
          :parts="message.parts"
          :pending-tool-calls="pendingToolCalls"
          :active="active && groupIndex === groups.length - 1"
          :timing="message.timing"
          :allow-branch="allowBranch && message.timing?.status === 'completed'"
          @branch="emit('branch', message.messageId)"
        />
        <div v-else class="space-y-2">
          <AiMessagePart
            v-for="(part, index) in message.parts"
            :key="`${message.id}-${index}`"
            :part="part"
            :pending-kind="'toolCallId' in part ? pendingToolCalls?.find(call => call.tool_call_id === part.toolCallId)?.kind : undefined"
          />
        </div>
      </div>
    </article>
    <AiAssistantTurn
      v-if="active && groups.at(-1)?.role !== 'assistant'"
      :parts="[]"
      :active="true"
      :timing="groups.at(-1)?.timing"
    />
  </div>
</template>
