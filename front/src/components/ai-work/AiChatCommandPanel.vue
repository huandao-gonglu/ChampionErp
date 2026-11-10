<script setup lang="ts">
import type { ChatCommandEntry } from '@/composables/useChatCommands'

defineProps<{
  entries: ChatCommandEntry[]
  activeIndex: number
}>()

const emit = defineEmits<{
  (event: 'select', index: number): void
  (event: 'hover', index: number): void
}>()
</script>

<template>
  <!-- 斜杠命令候选面板：命令清单来自统一注册表 services/chatCommands，本组件不做任何枚举。
       当前不可用的命令恒定灰色禁用展示（不附原因），点击无效果。 -->
  <div
    role="listbox"
    aria-label="聊天命令"
    class="absolute bottom-full left-0 right-0 z-20 mb-2 max-h-56 overflow-y-auto rounded-xl border border-slate-200 bg-white py-1 shadow-lg dark:border-dark-600 dark:bg-dark-800"
    data-testid="ai-chat-command-panel"
  >
    <p class="px-3 pb-1 pt-1.5 text-[10px] font-bold uppercase tracking-wide text-slate-400 dark:text-accent-400">
      可用命令
    </p>
    <button
      v-for="(entry, index) in entries"
      :key="entry.command.name"
      type="button"
      role="option"
      :aria-selected="index === activeIndex"
      :aria-disabled="entry.enabled ? undefined : 'true'"
      class="flex w-full items-start gap-2 px-3 py-2 text-left transition"
      :class="[
        entry.enabled
          ? index === activeIndex
            ? 'bg-primary-50 dark:bg-primary-500/10'
            : 'hover:bg-slate-50 dark:hover:bg-dark-700'
          : 'cursor-not-allowed opacity-45',
      ]"
      :data-testid="`ai-chat-command-${entry.command.name}`"
      @click="entry.enabled && emit('select', index)"
      @mouseenter="emit('hover', index)"
    >
      <span class="shrink-0 rounded-md bg-slate-100 px-1.5 py-0.5 font-mono text-xs font-bold text-primary-600 dark:bg-dark-700 dark:text-primary-300">
        /{{ entry.command.name }}
      </span>
      <span class="min-w-0">
        <span class="block text-xs font-semibold text-slate-800 dark:text-white">{{ entry.command.title }}</span>
        <span class="block truncate text-[11px] text-slate-400 dark:text-accent-400">{{ entry.command.description }}</span>
      </span>
    </button>
  </div>
</template>
