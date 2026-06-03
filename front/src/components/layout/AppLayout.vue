<script setup lang="ts">
import AppHeader from './AppHeader.vue'
</script>

<template>
  <div class="min-h-screen bg-accent-100 text-accent-950 dark:bg-dark-950 dark:text-accent-100">
    <div class="relative min-h-screen transition-all duration-300">
      <AppHeader />
      <main class="p-4 md:p-6 lg:p-8">
        <slot />
      </main>
    </div>
  </div>
</template>
