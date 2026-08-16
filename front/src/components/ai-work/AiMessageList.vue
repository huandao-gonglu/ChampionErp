<script setup lang="ts">
import type { UIMessage } from 'ai'
import AiMessagePart from './AiMessagePart.vue'

defineProps<{
  messages: UIMessage[]
}>()
</script>

<template>
  <div class="space-y-4" data-testid="ai-message-list">
    <article
      v-for="message in messages"
      :key="message.id"
      class="flex"
      :class="message.role === 'user' ? 'justify-end' : 'justify-start'"
      :data-testid="`ai-message-${message.role}`"
    >
      <div
        class="max-w-[82%] rounded-2xl px-4 py-3 text-sm leading-6 shadow-sm"
        :class="message.role === 'user'
          ? 'rounded-br-md bg-primary-600 text-white'
          : 'rounded-bl-md border border-slate-200 bg-white text-slate-800 dark:border-dark-700 dark:bg-dark-800 dark:text-accent-100'"
      >
        <p
          class="mb-1 text-[10px] font-black uppercase tracking-wider opacity-70"
        >
          {{ message.role === 'user' ? '你' : '全局 Agent' }}
        </p>
        <div class="space-y-2">
          <AiMessagePart
            v-for="(part, index) in message.parts"
            :key="`${message.id}-${index}`"
            :part="part"
          />
        </div>
      </div>
    </article>
  </div>
</template>
