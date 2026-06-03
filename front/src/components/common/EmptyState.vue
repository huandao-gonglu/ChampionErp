<script setup lang="ts">
defineProps<{
  title: string
  description?: string
}>()
</script>

<template>
  <div class="rounded-2xl border border-dashed border-accent-300 bg-white/70 p-8 text-center dark:border-dark-600 dark:bg-dark-900/50">
    <h3 class="text-base font-semibold text-accent-950 dark:text-white">{{ title }}</h3>
    <p v-if="description" class="mt-2 text-sm text-accent-500 dark:text-accent-300">{{ description }}</p>
    <div class="mt-4"><slot /></div>
  </div>
</template>
