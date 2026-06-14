<script setup lang="ts">
const props = defineProps<{
  logs: string[]
}>()
</script>

<template>
  <section class="rounded-lg border border-accent-200 bg-white p-5 shadow-card dark:border-dark-700 dark:bg-dark-900/80">
    <h2 class="card-title">运行日志</h2>
    <div class="mt-4 max-h-72 space-y-2 overflow-y-auto pr-2">
      <p v-for="log in props.logs" :key="log" class="rounded-lg bg-accent-50 px-3 py-2 text-sm text-accent-700 ring-1 ring-accent-200 dark:bg-dark-950/70 dark:text-accent-200 dark:ring-dark-700">
        {{ log }}
      </p>
    </div>
  </section>
</template>
