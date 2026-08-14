<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()
const shouldRender = computed(() => route.name !== 'AiWork')
</script>

<template>
  <div
    v-if="shouldRender"
    data-testid="ai-work-floating"
    class="fixed bottom-5 right-5 z-[70]"
  >
    <a
      href="/aiWork"
      target="_blank"
      rel="noopener noreferrer"
      aria-label="打开 AI 对话检查器"
      title="打开 AI 对话检查器"
      class="flex size-14 items-center justify-center rounded-full border border-white/60 bg-primary-600 text-white shadow-xl shadow-primary-950/25 transition duration-200 hover:-translate-y-1 hover:bg-primary-500 hover:shadow-2xl hover:shadow-primary-950/30 focus:outline-none focus-visible:ring-4 focus-visible:ring-primary-300 dark:border-primary-300/20 dark:bg-primary-500 dark:hover:bg-primary-400"
    >
      <svg
        aria-hidden="true"
        viewBox="0 0 24 24"
        fill="none"
        class="size-7"
        stroke="currentColor"
        stroke-width="1.8"
        stroke-linecap="round"
        stroke-linejoin="round"
      >
        <path d="M5 3.5h14a1.5 1.5 0 0 1 1.5 1.5v14a1.5 1.5 0 0 1-1.5 1.5H5A1.5 1.5 0 0 1 3.5 19V5A1.5 1.5 0 0 1 5 3.5Z" />
        <path d="M7.5 8h9" />
        <path d="M7.5 12h6" />
        <path d="M7.5 16h7.5" />
      </svg>
    </a>
  </div>
</template>
