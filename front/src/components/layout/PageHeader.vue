<script setup lang="ts">
const props = withDefaults(defineProps<{
  title: string
  description?: string
  eyebrow?: string
}>(), {
  description: '',
  eyebrow: 'Champion ERP',
})
</script>

<template>
  <header class="rounded-3xl border border-primary-100 bg-white p-6 text-accent-950 shadow-card dark:border-primary-500/20 dark:bg-dark-900/80 dark:text-white">
    <div class="flex flex-col gap-5 xl:flex-row xl:items-end xl:justify-between">
      <div class="min-w-0">
        <p class="text-xs font-semibold uppercase tracking-[0.22em] text-primary-700 dark:text-primary-300">{{ props.eyebrow }}</p>
        <h2 class="mt-3 text-3xl font-bold">{{ props.title }}</h2>
        <p v-if="props.description" class="mt-3 max-w-3xl text-sm leading-6 text-accent-600 dark:text-accent-300">{{ props.description }}</p>
      </div>
      <div v-if="$slots.actions" class="flex flex-wrap items-center gap-3">
        <slot name="actions" />
      </div>
    </div>
  </header>
</template>
