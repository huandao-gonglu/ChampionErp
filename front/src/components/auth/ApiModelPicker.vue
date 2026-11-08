<script setup lang="ts">
import { computed, ref, useId } from 'vue'

type ModelOption = { id: string; label: string }
const props = defineProps<{
  value: string
  disabled: boolean
  canLoad: boolean
  loadOptions: () => Promise<ModelOption[]>
}>()
const emit = defineEmits<{ change: [value: string] }>()
const listId = useId()
const open = ref(false)
const loading = ref(false)
const error = ref('')
const options = ref<ModelOption[]>([])
const query = ref('')
const active = ref(-1)
let requestId = 0
const visible = computed(() => options.value.filter(item =>
  (item.id + ' ' + item.label).toLowerCase().includes(query.value.toLowerCase()),
))

function close() {
  open.value = false
  active.value = -1
  requestId++
}

async function show() {
  if (props.disabled || !props.canLoad || open.value) return
  const id = ++requestId
  open.value = true
  loading.value = true
  options.value = []
  query.value = ''
  error.value = ''
  try {
    const result = await props.loadOptions()
    if (id === requestId) options.value = result
  } catch (exc) {
    if (id === requestId) error.value = exc instanceof Error ? exc.message : '加载模型列表失败'
  } finally {
    if (id === requestId) loading.value = false
  }
}

function move(step: number) {
  if (!open.value) {
    void show()
    return
  }
  if (!visible.value.length) return
  active.value = Math.max(0, Math.min(visible.value.length - 1, active.value + step))
  document.getElementById(listId + '-' + active.value)?.scrollIntoView?.({ block: 'nearest' })
}

function select(option?: ModelOption) {
  if (option) emit('change', option.id)
  close()
}

function input(event: Event) {
  query.value = (event.target as HTMLInputElement).value
  active.value = -1
  emit('change', query.value)
}
</script>

<template>
  <div class="relative">
    <label :for="listId + '-input'" class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">模型</label>
    <input
      :id="listId + '-input'" class="input" :role="canLoad ? 'combobox' : undefined" :aria-autocomplete="canLoad ? 'list' : undefined" :aria-expanded="canLoad ? open : undefined"
      :aria-controls="open ? listId : undefined" :aria-activedescendant="active >= 0 ? listId + '-' + active : undefined"
      :value="value" :disabled="disabled" autocomplete="off" :placeholder="canLoad ? '选择或输入模型 ID' : '输入模型 ID'"
      @click="show" @blur="close" @input="input" @keydown.down.prevent="move(1)" @keydown.up.prevent="move(-1)"
      @keydown.enter="open && select(visible[active])" @keydown.esc="close"
    />
    <div v-if="open" class="absolute z-20 mt-1 w-full rounded-lg border border-accent-200 bg-white shadow-lg dark:border-dark-700 dark:bg-dark-900">
      <p v-if="loading || error || !visible.length" class="px-3 py-2 text-sm" role="status">{{ loading ? '正在加载模型列表…' : error || '没有匹配的模型，可直接填写模型 ID。' }}</p>
      <ul :id="listId" role="listbox" aria-label="可选模型" class="max-h-56 overflow-y-auto">
        <li
          v-for="(option, index) in visible" :id="listId + '-' + index" :key="option.id" role="option"
          :aria-selected="option.id === value" class="cursor-pointer break-all px-3 py-1.5 text-sm hover:bg-accent-50 dark:hover:bg-dark-700"
          :class="{ 'bg-accent-50 dark:bg-dark-700': index === active }"
          @mousedown.prevent @click="select(option)"
        >
          {{ option.label }}
        </li>
      </ul>
    </div>
  </div>
</template>
