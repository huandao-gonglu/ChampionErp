<script setup lang="ts">
import { ref, watch } from 'vue'

const props = defineProps<{ src: string; alt: string }>()
const failed = ref(false)
const attempt = ref(0)
watch(() => props.src, () => { failed.value = false; attempt.value = 0 })
function retry() { failed.value = false; attempt.value++ }
</script>

<template>
  <div class="overflow-hidden rounded-lg border border-accent-200 bg-white dark:border-dark-700 dark:bg-dark-950">
    <img v-if="src && !failed" :key="attempt" :src="src" :alt="alt" referrerpolicy="no-referrer" class="size-full object-contain" @error="failed = true" />
    <div v-else class="flex size-full flex-col items-center justify-center gap-1 p-2 text-center text-xs text-accent-500">
      <span>{{ src ? '图片加载失败' : '暂无预览' }}</span>
      <button v-if="src" type="button" :aria-label="`重新加载${alt}`" class="text-primary-700 dark:text-primary-300" @click="retry">重试</button>
    </div>
  </div>
</template>
