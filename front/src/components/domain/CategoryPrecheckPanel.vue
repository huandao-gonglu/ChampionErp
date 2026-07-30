<script setup lang="ts">
import { computed } from 'vue'
import type { CategoryPrecheckResult } from '@/types/workflow'

const props = defineProps<{
  result: CategoryPrecheckResult | null
}>()

const issues = computed(() => [
  ...(props.result?.missingFields || []),
  ...(props.result?.errors || []),
])
</script>

<template>
  <div
    v-if="props.result"
    class="mt-4 rounded-2xl p-3 text-sm ring-1"
    :class="props.result.ok
      ? 'bg-emerald-50 text-emerald-800 ring-emerald-200'
      : 'bg-amber-50 text-amber-800 ring-amber-200'"
    role="status"
  >
    <div class="font-semibold">{{ props.result.ok ? '类目预检通过' : '类目预检需处理' }}</div>
    <ul v-if="issues.length" class="mt-2 list-inside list-disc">
      <li v-for="item in issues" :key="item">{{ item }}</li>
    </ul>
  </div>
</template>
