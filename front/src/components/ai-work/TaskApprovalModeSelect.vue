<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { onClickOutside } from '@vueuse/core'
import {
  PhCaretDown,
  PhCheck,
  PhShieldCheck,
  PhShieldWarning,
} from '@phosphor-icons/vue'
import type { TaskApprovalMode } from '@/api/taskApprovalMode'
import { useTaskApprovalModeStore } from '@/stores/taskApprovalMode'

const store = useTaskApprovalModeStore()
const rootRef = ref<HTMLElement | null>(null)
const open = ref(false)

const label = computed(() => (
  store.mode === 'full' ? '完全授权' : '询问审批'
))

onClickOutside(rootRef, () => {
  open.value = false
})

onMounted(() => {
  void store.ensureLoaded()
})

async function choose(mode: TaskApprovalMode): Promise<void> {
  if (mode === store.mode) {
    open.value = false
    return
  }
  if (
    mode === 'full'
    && !window.confirm(
      '切换为“完全授权”后，删除、发布、远端关闭和创建运单等高风险任务将不再逐项询问，系统会按当前设置自动批准。是否继续？',
    )
  ) {
    return
  }
  const saved = await store.setMode(mode)
  if (saved) open.value = false
}
</script>

<template>
  <div ref="rootRef" class="relative">
    <button
      type="button"
      class="group flex h-9 items-center gap-1.5 rounded-xl px-2.5 text-sm font-bold transition focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-400/70"
      :class="store.mode === 'full'
        ? 'text-orange-500 hover:bg-orange-50 dark:text-orange-400 dark:hover:bg-orange-500/10'
        : 'text-slate-600 hover:bg-slate-100 dark:text-accent-200 dark:hover:bg-dark-700'"
      :aria-expanded="open"
      aria-haspopup="menu"
      data-testid="task-approval-mode-trigger"
      :disabled="store.busy"
      @click="open = !open"
      @keydown.esc="open = false"
    >
      <PhShieldWarning v-if="store.mode === 'full'" :size="21" weight="regular" />
      <PhShieldCheck v-else :size="21" weight="regular" />
      <span>{{ store.busy ? '正在保存…' : label }}</span>
      <PhCaretDown :size="13" weight="bold" class="opacity-60" />
    </button>

    <div
      v-if="open"
      role="menu"
      class="absolute bottom-full left-0 z-30 mb-2 w-64 overflow-hidden rounded-2xl border border-slate-200 bg-white p-1.5 shadow-xl shadow-slate-950/15 dark:border-dark-600 dark:bg-dark-800"
      data-testid="task-approval-mode-menu"
    >
      <button
        type="button"
        role="menuitemradio"
        :aria-checked="store.mode === 'ask'"
        class="flex w-full items-start gap-2.5 rounded-xl px-3 py-2.5 text-left transition hover:bg-slate-100 dark:hover:bg-dark-700"
        data-testid="task-approval-mode-ask"
        @click="choose('ask')"
      >
        <PhShieldCheck :size="20" class="mt-0.5 shrink-0 text-slate-500 dark:text-accent-300" />
        <span class="min-w-0 flex-1">
          <span class="block text-sm font-black text-slate-800 dark:text-white">询问审批</span>
          <span class="mt-0.5 block text-xs leading-5 text-slate-500 dark:text-accent-300">高风险操作执行前等待你确认。</span>
        </span>
        <PhCheck v-if="store.mode === 'ask'" :size="17" weight="bold" class="mt-0.5 text-primary-600" />
      </button>
      <button
        type="button"
        role="menuitemradio"
        :aria-checked="store.mode === 'full'"
        class="flex w-full items-start gap-2.5 rounded-xl px-3 py-2.5 text-left transition hover:bg-orange-50 dark:hover:bg-orange-500/10"
        data-testid="task-approval-mode-full"
        @click="choose('full')"
      >
        <PhShieldWarning :size="20" class="mt-0.5 shrink-0 text-orange-500 dark:text-orange-400" />
        <span class="min-w-0 flex-1">
          <span class="block text-sm font-black text-orange-600 dark:text-orange-300">完全授权</span>
          <span class="mt-0.5 block text-xs leading-5 text-slate-500 dark:text-accent-300">自动批准现有 Capability 中需要审批的操作。</span>
        </span>
        <PhCheck v-if="store.mode === 'full'" :size="17" weight="bold" class="mt-0.5 text-orange-500" />
      </button>
      <p v-if="store.error" role="alert" class="px-3 pb-2 pt-1 text-xs text-rose-600 dark:text-rose-300">
        {{ store.error }}
      </p>
    </div>
  </div>
</template>

