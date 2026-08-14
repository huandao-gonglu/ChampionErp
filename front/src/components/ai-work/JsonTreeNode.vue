<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(defineProps<{
  value: unknown
  label?: string
  depth?: number
}>(), {
  label: '',
  depth: 0,
})

const entries = computed<Array<[string, unknown]>>(() => {
  if (Array.isArray(props.value)) {
    return props.value.map((item, index) => [`[${index}]`, item])
  }
  if (props.value && typeof props.value === 'object') {
    return Object.entries(props.value as Record<string, unknown>)
  }
  return []
})

const isContainer = computed(() => Array.isArray(props.value) || (
  props.value !== null && typeof props.value === 'object'
))

const containerSummary = computed(() => {
  if (Array.isArray(props.value)) return `Array(${entries.value.length})`
  return `Object(${entries.value.length})`
})

const emptyContainerValue = computed(() => Array.isArray(props.value) ? '[]' : '{}')

const scalarClass = computed(() => {
  if (props.value === null) return 'text-fuchsia-600 dark:text-fuchsia-300'
  if (typeof props.value === 'string') return 'text-emerald-700 dark:text-emerald-300'
  if (typeof props.value === 'number') return 'text-sky-700 dark:text-sky-300'
  if (typeof props.value === 'boolean') return 'text-amber-700 dark:text-amber-300'
  return 'text-slate-600 dark:text-accent-300'
})

function formatScalar(value: unknown): string {
  if (value === null) return 'null'
  if (typeof value === 'string') return JSON.stringify(value)
  if (typeof value === 'undefined') return 'undefined'
  try {
    const serialized = JSON.stringify(value)
    return serialized === undefined ? String(value) : serialized
  } catch {
    return String(value)
  }
}
</script>

<template>
  <div class="min-w-0 font-mono text-xs leading-6" data-testid="json-tree-node">
    <details v-if="isContainer && entries.length" :open="depth === 0">
      <summary class="cursor-pointer select-none rounded px-1 hover:bg-slate-100 dark:hover:bg-dark-800">
        <span v-if="label" class="font-bold text-violet-700 dark:text-violet-300">{{ label }}</span>
        <span v-if="label" class="text-slate-400">: </span>
        <span class="text-slate-500 dark:text-accent-300">{{ containerSummary }}</span>
      </summary>
      <div class="ml-3 border-l border-slate-200 pl-3 dark:border-dark-700">
        <JsonTreeNode
          v-for="([entryLabel, entryValue], index) in entries"
          :key="`${entryLabel}-${index}`"
          :value="entryValue"
          :label="entryLabel"
          :depth="depth + 1"
        />
      </div>
    </details>

    <div v-else class="flex min-w-0 gap-1 rounded px-1 hover:bg-slate-100 dark:hover:bg-dark-800">
      <span v-if="label" class="shrink-0 font-bold text-violet-700 dark:text-violet-300">{{ label }}</span>
      <span v-if="label" class="shrink-0 text-slate-400">:</span>
      <span v-if="isContainer" class="break-all text-slate-500 dark:text-accent-300">
        {{ emptyContainerValue }}
      </span>
      <span v-else class="break-all" :class="scalarClass">{{ formatScalar(value) }}</span>
    </div>
  </div>
</template>
