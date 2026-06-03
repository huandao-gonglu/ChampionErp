<template>
  <section class="space-y-4">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <div><slot name="title" /></div>
      <div class="flex flex-wrap gap-2"><slot name="actions" /></div>
    </div>
    <div v-if="$slots.filters" class="card"><slot name="filters" /></div>
    <div class="card overflow-hidden p-0"><slot name="table" /></div>
    <div v-if="$slots.pagination"><slot name="pagination" /></div>
  </section>
</template>
