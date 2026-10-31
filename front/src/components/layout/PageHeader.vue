<script setup lang="ts">
const props = withDefaults(defineProps<{
  title: string
  description?: string
}>(), {
  description: '',
})
</script>

<template>
  <header class="flex flex-wrap items-start justify-between gap-4 text-accent-950 dark:text-white">
    <div class="min-w-0">
      <h1 class="text-2xl font-bold">{{ props.title }}</h1>
      <p v-if="props.description" class="muted mt-2">{{ props.description }}</p>
      <slot />
    </div>
    <div v-if="$slots.actions" class="flex flex-wrap items-center gap-3">
      <slot name="actions" />
    </div>
  </header>
</template>
