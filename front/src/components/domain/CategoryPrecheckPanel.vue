<script setup lang="ts">
import { computed } from 'vue'
import type { CategoryPrecheckResult } from '@/types/workflow'

const props = withDefaults(defineProps<{
  result: CategoryPrecheckResult | null
  canLocateAttribute?: boolean
}>(), { canLocateAttribute: true })

const emit = defineEmits<{
  locateAttribute: [attributeId: string]
}>()

const issues = computed(() => [...new Set([
  ...(props.result?.missingFields || []),
  ...(props.result?.errors || []),
])])

function attributeIdFromIssue(issue: string) {
  const value = String(issue || '').trim()
  return value.startsWith('attributes.') ? value.slice('attributes.'.length) : ''
}
</script>

<template>
  <div
    v-if="props.result"
    class="rounded-xl p-3 text-sm ring-1"
    :class="props.result.ok
      ? 'bg-emerald-50 text-emerald-800 ring-emerald-200 dark:bg-emerald-950/30 dark:text-emerald-200 dark:ring-emerald-800/60'
      : 'bg-amber-50 text-amber-800 ring-amber-200 dark:bg-amber-950/30 dark:text-amber-200 dark:ring-amber-800/60'"
    role="status"
  >
    <div class="font-semibold">{{ props.result.ok ? '类目预检通过' : '类目预检需处理' }}</div>
    <ul v-if="issues.length" class="mt-2 list-inside list-disc space-y-1">
      <li v-for="item in issues" :key="item">
        <button
          v-if="props.canLocateAttribute && attributeIdFromIssue(item)"
          class="rounded px-1 text-left underline decoration-dotted underline-offset-2 transition hover:bg-amber-100 focus:outline-none focus:ring-2 focus:ring-amber-400 dark:hover:bg-amber-900/50"
          type="button"
          :title="`定位到属性 ${attributeIdFromIssue(item)}`"
          @click="emit('locateAttribute', attributeIdFromIssue(item))"
        >
          {{ item }}
        </button>
        <span v-else>{{ item }}</span>
      </li>
    </ul>
  </div>
</template>
