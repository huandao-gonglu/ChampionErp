<script setup lang="ts">
const props = defineProps<{
  logs: string[]
}>()
</script>

<template>
  <section class="card">
    <h2 class="card-title">运行日志</h2>
    <div class="mt-4 max-h-72 space-y-2 overflow-y-auto pr-2">
      <p v-for="log in props.logs" :key="log" class="rounded-xl bg-slate-50 px-3 py-2 text-sm text-slate-700">
        {{ log }}
      </p>
    </div>
  </section>
</template>
