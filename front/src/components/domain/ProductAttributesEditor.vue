<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { PhMagnifyingGlass, PhPlus, PhTrash, PhX } from '@phosphor-icons/vue'

const props = defineProps<{
  modelValue: Record<string, unknown>
  title: string
  description: string
  emptyMessage: string
  hint?: string
  disabled?: boolean
}>()

const emit = defineEmits<{
  'update:modelValue': [value: Record<string, unknown>]
  'validity-change': [valid: boolean]
}>()

type AttributeRow = { id: number; name: string; value: string; originalValue: unknown; originalText: string }
let nextId = 0
const rows = ref<AttributeRow[]>([])
const search = ref('')
const error = computed(() => {
  const names = new Set<string>()
  for (const row of rows.value) {
    const name = row.name.trim()
    if (!name) return '请填写属性名称，或删除空白行后保存。'
    if (names.has(name)) return `属性名称“${name}”重复，请修改后保存。`
    names.add(name)
  }
  return ''
})

function attributesFromRows() {
  return Object.fromEntries(rows.value.map((row) => [
    row.name.trim(),
    row.value === row.originalText ? row.originalValue : row.value,
  ]))
}

watch(() => props.modelValue, (attributes) => {
  // 自己提交的值不重建行，保证输入焦点及尚未完成的属性名编辑稳定。
  if (JSON.stringify(attributes) === JSON.stringify(attributesFromRows())) return
  rows.value = Object.entries(attributes).map(([name, value]) => {
    const text = value !== null && typeof value === 'object' ? JSON.stringify(value) : String(value ?? '')
    // 未编辑的布尔值、数组和对象保持原始类型，避免保存其他字段时损坏属性。
    return { id: nextId++, name, value: text, originalValue: value, originalText: text }
  })
}, { immediate: true, deep: true })

watch(rows, () => {
  emit('validity-change', !error.value)
  if (!error.value) emit('update:modelValue', attributesFromRows())
}, { deep: true, flush: 'sync' })
emit('validity-change', !error.value)

const visibleRows = computed(() => {
  const query = search.value.trim().toLocaleLowerCase()
  return rows.value.filter((row) => `${row.name} ${row.value}`.toLocaleLowerCase().includes(query))
})

function addAttribute() {
  search.value = ''
  rows.value.push({ id: nextId++, name: '', value: '', originalValue: '', originalText: '' })
}

function removeAttribute(id: number) {
  rows.value = rows.value.filter((row) => row.id !== id)
}
</script>

<template>
  <section class="mt-4 rounded-2xl border border-accent-200/90 bg-white p-4 shadow-sm first:mt-0 dark:border-dark-700 dark:bg-dark-800/70" :aria-label="props.title">
    <div class="flex flex-wrap items-start justify-between gap-3">
      <div class="min-w-0">
        <h3 class="flex items-center gap-2 text-sm font-bold text-accent-950 dark:text-white">
          <span>{{ props.title }}</span>
          <span class="rounded-full bg-accent-100 px-2 py-0.5 text-xs font-semibold tabular-nums text-accent-600 dark:bg-dark-700 dark:text-accent-200">{{ rows.length }} 项</span>
        </h3>
        <p class="mt-1 text-xs text-accent-500 dark:text-accent-400">{{ props.description }}</p>
      </div>
      <div class="flex flex-wrap items-center gap-2">
        <div v-if="rows.length" class="relative w-48 sm:w-56">
          <PhMagnifyingGlass :size="14" aria-hidden="true" class="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-accent-400" />
          <label class="block">
            <span class="sr-only">搜索{{ props.title }}</span>
            <input v-model="search" type="search" class="input h-8 !rounded-xl !py-0 !pl-8 !pr-7 text-xs" placeholder="搜索属性名称或值" />
          </label>
          <button
            v-if="search"
            type="button"
            aria-label="清空属性搜索"
            class="absolute right-1.5 top-1/2 -translate-y-1/2 rounded-md p-0.5 text-accent-400 hover:bg-accent-100 hover:text-accent-700 dark:hover:bg-dark-800"
            @click="search = ''"
          >
            <PhX :size="12" aria-hidden="true" />
          </button>
        </div>
        <button type="button" class="btn btn-outline !min-h-8 !rounded-xl !px-3 !py-1 text-xs" :disabled="props.disabled" @click="addAttribute">
          <PhPlus :size="13" weight="bold" aria-hidden="true" />添加属性
        </button>
      </div>
    </div>

    <p v-if="props.hint" class="mt-2 rounded-lg bg-accent-50 px-3 py-1.5 text-xs text-accent-600 dark:bg-dark-900/60 dark:text-accent-300">{{ props.hint }}</p>
    <p v-if="error" class="mt-2.5 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs font-medium text-rose-700 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-200" role="alert">{{ error }}</p>
    <p v-if="!rows.length" class="mt-3 rounded-xl border border-dashed border-accent-200 py-6 text-center text-xs text-accent-500 dark:border-dark-700 dark:text-accent-400">{{ props.emptyMessage }}</p>
    <p v-else-if="!visibleRows.length" class="mt-3 py-4 text-center text-xs text-accent-500 dark:text-accent-400">没有匹配的属性。</p>

    <div v-else class="mt-3 grid gap-x-4 gap-y-2 xl:grid-cols-2">
      <div v-for="row in visibleRows" :key="row.id" class="flex items-start gap-2" data-testid="product-attribute-row">
        <label class="w-1/3 min-w-0">
          <span class="sr-only">属性名称</span>
          <input v-model="row.name" class="input block h-9 !rounded-lg py-1.5 text-xs font-medium" :disabled="props.disabled" :title="row.name" placeholder="属性名称" />
        </label>
        <label class="min-w-0 flex-1">
          <span class="sr-only">属性值</span>
          <textarea v-model="row.value" class="input block h-9 min-h-9 resize-y !rounded-lg py-1.5 text-xs" :disabled="props.disabled" :title="row.value" placeholder="属性值" rows="1" />
        </label>
        <button
          type="button"
          class="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-accent-400 transition hover:bg-rose-50 hover:text-rose-600 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 disabled:cursor-not-allowed disabled:opacity-50 dark:hover:bg-rose-500/10 dark:hover:text-rose-400"
          :disabled="props.disabled"
          :aria-label="`删除属性 ${row.name || '未命名'}`"
          :title="`删除属性 ${row.name || '未命名'}`"
          @click="removeAttribute(row.id)"
        >
          <PhTrash :size="16" aria-hidden="true" />
        </button>
      </div>
    </div>
  </section>
</template>
