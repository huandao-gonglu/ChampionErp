<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { APP_NAME } from '@/constants/branding'

const route = useRoute()

const title = computed(() => (route.meta.title || APP_NAME))
const description = computed(() => (route.meta.description || ''))
</script>

<template>
  <header class="sticky top-0 z-30 border-b border-accent-200/80 bg-white/85 px-4 py-3 backdrop-blur dark:border-dark-700 dark:bg-dark-900/85">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <div>
        <p class="text-xs font-semibold tracking-[0.18em] text-primary-600 dark:text-primary-300">{{ APP_NAME }}</p>
        <h1 class="text-xl font-black text-accent-950 dark:text-white">{{ title }}</h1>
        <p v-if="description" class="text-sm text-accent-500 dark:text-accent-300">{{ description }}</p>
      </div>
    </div>
  </header>
</template>
