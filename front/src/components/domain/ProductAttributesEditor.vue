<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { PhTrash } from '@phosphor-icons/vue'

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
  <section class="mt-5 border-t border-slate-200 pt-5 dark:border-dark-700" :aria-label="props.title">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <div>
        <h3 class="text-sm font-semibold text-slate-900 dark:text-white">{{ props.title }} <span class="text-slate-500">（{{ rows.length }} 项）</span></h3>
        <p class="muted mt-1">{{ props.description }}</p>
      </div>
      <button type="button" class="btn btn-outline" :disabled="props.disabled" @click="addAttribute">添加属性</button>
    </div>
    <p v-if="props.hint" class="muted mt-2">{{ props.hint }}</p>
    <label v-if="rows.length" class="mt-3 block max-w-sm">
      <span class="sr-only">搜索{{ props.title }}</span>
      <input v-model="search" type="search" class="input" placeholder="搜索属性名称或值" />
    </label>
    <p v-if="error" class="mt-3 text-sm text-red-600" role="alert">{{ error }}</p>
    <p v-if="!rows.length" class="muted mt-3">{{ props.emptyMessage }}</p>
    <p v-else-if="!visibleRows.length" class="muted mt-3">没有匹配的属性。</p>
    <div class="mt-3 grid gap-x-4 gap-y-2 xl:grid-cols-2">
      <div v-for="row in visibleRows" :key="row.id" class="flex items-start gap-2" data-testid="product-attribute-row">
        <label class="w-1/3 min-w-0">
          <span class="sr-only">属性名称</span>
          <input v-model="row.name" class="input block h-9 rounded-lg py-1.5" :disabled="props.disabled" :title="row.name" placeholder="属性名称" />
        </label>
        <label class="min-w-0 flex-1">
          <span class="sr-only">属性值</span>
          <textarea v-model="row.value" class="input block h-9 min-h-9 resize-y rounded-lg py-1.5" :disabled="props.disabled" :title="row.value" placeholder="属性值" rows="1" />
        </label>
        <button
          type="button"
          class="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-slate-400 transition hover:bg-red-50 hover:text-red-600 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 disabled:cursor-not-allowed disabled:opacity-50 dark:hover:bg-red-500/10 dark:hover:text-red-400"
          :disabled="props.disabled"
          :aria-label="`删除属性 ${row.name || '未命名'}`"
          :title="`删除属性 ${row.name || '未命名'}`"
          @click="removeAttribute(row.id)"
        >
          <PhTrash :size="18" aria-hidden="true" />
        </button>
      </div>
    </div>
  </section>
</template>
