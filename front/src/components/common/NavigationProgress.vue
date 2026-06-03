<script setup lang="ts">
import { useAppStore } from '@/stores/app'

const app = useAppStore()
</script>

<template>
  <div v-if="app.globalLoading" class="fixed inset-x-0 top-0 z-[60] h-1 overflow-hidden bg-primary-100 dark:bg-dark-800">
    <div class="h-full w-1/2 animate-pulse rounded-r-full bg-primary-500" />
  </div>
</template>
