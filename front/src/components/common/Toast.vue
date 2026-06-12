<script setup lang="ts">
import { useAppStore } from '@/stores/app'
import type { ToastKind } from '@/stores/app'

const app = useAppStore()

const kindClass: Record<ToastKind, string> = {
  success: 'border-emerald-200 bg-emerald-50 text-emerald-900 ring-emerald-100 dark:border-emerald-500/50 dark:bg-dark-900 dark:text-emerald-100 dark:ring-emerald-500/25',
  info: 'border-blue-200 bg-blue-50 text-blue-900 ring-blue-100 dark:border-blue-500/50 dark:bg-dark-900 dark:text-blue-100 dark:ring-blue-500/25',
  warning: 'border-amber-200 bg-amber-50 text-amber-900 ring-amber-100 dark:border-amber-500/50 dark:bg-dark-900 dark:text-amber-100 dark:ring-amber-500/25',
  error: 'border-rose-200 bg-rose-50 text-rose-900 ring-rose-100 dark:border-rose-500/50 dark:bg-dark-900 dark:text-rose-100 dark:ring-rose-500/25',
}
</script>

<template>
  <div class="fixed right-4 top-4 z-50 w-full max-w-sm space-y-2">
    <button
      v-for="toast in app.toasts"
      :key="toast.id"
      type="button"
      class="w-full rounded-2xl border px-4 py-3 text-left text-sm font-semibold shadow-lg ring-1 shadow-accent-950/10 dark:shadow-black/30"
      :class="kindClass[toast.kind]"
      @click="app.dismissToast(toast.id)"
    >
      {{ toast.message }}
    </button>
  </div>
</template>
