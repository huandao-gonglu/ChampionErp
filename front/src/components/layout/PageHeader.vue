<script setup lang="ts">
const props = withDefaults(defineProps<{
  title: string
  description?: string
  sticky?: boolean
}>(), {
  description: '',
  sticky: false,
})
</script>

<template>
  <header
    class="flex flex-wrap items-start justify-between gap-4 text-accent-950 dark:text-white"
    :class="props.sticky ? 'page-header-sticky' : ''"
  >
    <div class="min-w-0">
      <div class="flex flex-wrap items-center gap-3">
        <slot name="leading" />
        <h1 class="text-2xl font-bold">{{ props.title }}</h1>
      </div>
      <p v-if="props.description" class="muted mt-2">{{ props.description }}</p>
      <slot />
    </div>
    <div v-if="$slots.actions" class="ml-auto flex min-w-0 flex-wrap items-center justify-end gap-2">
      <slot name="actions" />
    </div>
  </header>
</template>
