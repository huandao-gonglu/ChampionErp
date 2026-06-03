<script setup lang="ts">
import { useAppStore } from '@/stores/app'

const app = useAppStore()

const kindClass = {
  success: 'border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-100',
  info: 'border-blue-200 bg-blue-50 text-blue-800 dark:border-blue-500/30 dark:bg-blue-500/10 dark:text-blue-100',
  warning: 'border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-100',
  error: 'border-rose-200 bg-rose-50 text-rose-800 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-100',
}
</script>

<template>
  <div class="fixed right-4 top-4 z-50 w-full max-w-sm space-y-2">
    <button
      v-for="toast in app.toasts"
      :key="toast.id"
      type="button"
      class="w-full rounded-2xl border px-4 py-3 text-left text-sm font-medium shadow-card backdrop-blur"
      :class="kindClass[toast.kind]"
      @click="app.dismissToast(toast.id)"
    >
      {{ toast.message }}
    </button>
  </div>
</template>
