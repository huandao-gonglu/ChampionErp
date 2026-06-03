<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { APP_SHORT_NAME } from '@/constants/branding'
import { useAppStore } from '@/stores/app'

const route = useRoute()
const { t } = useI18n()
const app = useAppStore()

const title = computed(() => (typeof route.meta.titleKey === 'string' ? t(route.meta.titleKey) : route.meta.title || APP_SHORT_NAME))
const description = computed(() => (typeof route.meta.descriptionKey === 'string' ? t(route.meta.descriptionKey) : ''))
</script>

<template>
  <header class="sticky top-0 z-30 border-b border-accent-200/80 bg-white/85 px-4 py-3 backdrop-blur dark:border-dark-700 dark:bg-dark-900/85">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <div>
        <p class="text-xs font-semibold uppercase tracking-[0.18em] text-primary-600 dark:text-primary-300">{{ APP_SHORT_NAME }}</p>
        <h1 class="text-xl font-black text-accent-950 dark:text-white">{{ title }}</h1>
        <p v-if="description" class="text-sm text-accent-500 dark:text-accent-300">{{ description }}</p>
      </div>
      <button type="button" class="btn btn-outline" @click="app.toggleTheme()">
        {{ app.darkMode ? '浅色' : '深色' }}模式
      </button>
    </div>
  </header>
</template>
