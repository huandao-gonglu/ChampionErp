<script setup lang="ts">
const props = defineProps<{
  title: string
  description: string
}>()
</script>

<template>
  <header class="rounded-3xl bg-slate-950 p-6 text-white shadow-soft">
    <p class="text-xs font-semibold uppercase tracking-[0.22em] text-brand-100">Champion ERP</p>
    <h2 class="mt-3 text-3xl font-bold">{{ props.title }}</h2>
    <p class="mt-3 max-w-3xl text-sm leading-6 text-slate-300">{{ props.description }}</p>
  </header>
</template>
