<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { onClickOutside, useEventListener } from '@vueuse/core'
import { PhCaretDown, PhCheck, PhShield, PhShieldCheck, PhShieldWarning } from '@phosphor-icons/vue'
import type { AiToolApprovalMode } from '@/api/aiApprovalMode'
import { useAiApprovalModeStore } from '@/stores/aiApprovalMode'

const store = useAiApprovalModeStore()
const root = ref<HTMLElement | null>(null)
const open = ref(false)
const label = computed(() => (
  store.mode === 'full'
    ? '完全授权'
    : store.mode === 'auto'
      ? '自动审批'
      : store.mode === 'ask'
        ? '询问审批'
        : '权限设置'
))
const triggerColorClass = computed(() => (
  store.mode === 'full'
    ? 'text-orange-600 dark:text-orange-400'
    : store.mode === 'auto'
      ? 'text-sky-600 dark:text-sky-400'
      : 'text-slate-600 dark:text-accent-200'
))
const options: { mode: AiToolApprovalMode; label: string; hint: string }[] = [
  { mode: 'ask', label: '询问审批', hint: '所有需审批操作执行前均等待你确认。' },
  { mode: 'auto', label: '自动审批', hint: '尺寸估算、偏好更新、本地采购登记与收货仓绑定自动批准；发布、改价、删除、采购付款及买家消息仍需确认。' },
  { mode: 'full', label: '完全授权', hint: '全部审批操作均自动批准，无需逐次确认。' },
]

onClickOutside(root, () => { open.value = false })
onMounted(() => { void store.refresh() })
useEventListener(window, 'focus', () => { void store.refresh() })

function toggle(): void {
  open.value = !open.value
  if (open.value) void store.refresh()
}

async function choose(mode: AiToolApprovalMode): Promise<void> {
  if (await store.setMode(mode)) open.value = false
}
</script>

<template>
  <div ref="root" class="relative" @keydown.esc.stop="open = false">
    <button
      type="button"
      class="flex h-9 items-center gap-1.5 rounded-xl px-2 text-xs font-bold hover:bg-slate-100 focus-visible:ring-2 focus-visible:ring-primary-400 dark:hover:bg-dark-700"
      :class="triggerColorClass"
      aria-label="操作确认设置"
      aria-haspopup="menu"
      :aria-expanded="open"
      data-testid="ai-approval-mode-trigger"
      @click="toggle"
    >
      <PhShieldWarning v-if="store.mode === 'full'" :size="20" />
      <PhShield v-else-if="store.mode === 'auto'" :size="20" />
      <PhShieldCheck v-else :size="20" />
      <span>{{ store.busy ? '读取或保存中…' : label }}</span>
      <PhCaretDown :size="12" />
    </button>
    <div
      v-if="open"
      role="menu"
      class="absolute bottom-full left-0 z-30 mb-2 w-72 max-w-[calc(100vw-4rem)] rounded-xl border border-slate-200 bg-white p-1.5 shadow-xl dark:border-dark-600 dark:bg-dark-800"
      data-testid="ai-approval-mode-menu"
    >
      <button
        v-for="option in options"
        :key="option.mode"
        type="button"
        role="menuitemradio"
        :aria-checked="store.mode === option.mode"
        :disabled="store.busy || store.mode === null"
        class="flex w-full items-start gap-2 rounded-lg px-3 py-2 text-left hover:bg-slate-100 disabled:opacity-50 dark:hover:bg-dark-700"
        :data-testid="`ai-approval-mode-${option.mode}`"
        @click="choose(option.mode)"
      >
        <span class="flex-1">
          <span class="block text-sm font-bold text-slate-800 dark:text-white">{{ option.label }}</span>
          <span class="mt-1 block text-xs leading-5 text-slate-500 dark:text-accent-300">{{ option.hint }}</span>
        </span>
        <PhCheck v-if="store.mode === option.mode" :size="17" class="shrink-0 text-primary-600" />
      </button>
    </div>
    <p v-if="store.error" role="alert" class="absolute bottom-full left-0 z-40 mb-2 w-64 rounded-lg bg-rose-50 p-3 text-xs text-rose-700 dark:bg-rose-950 dark:text-rose-200">
      {{ store.error }} <button type="button" class="underline" :disabled="store.busy" @click="store.refresh">重试</button>
    </p>
  </div>
</template>
