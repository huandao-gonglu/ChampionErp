<script setup lang="ts">
import { attributeConstraintError, attributeConstraintHint } from '@/utils/categoryConstraints'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { fetchOnlineAttributeValues, type OnlineAttributeDefinition, type OnlineAttributeOption, type OnlineAttributeRow } from '@/api/onlineProducts'

const props = defineProps<{ listingId: string; version: string; definition: OnlineAttributeDefinition; modelValue: OnlineAttributeRow[]; disabled: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [rows: OnlineAttributeRow[]] }>()
const query = ref('')
const options = ref<OnlineAttributeOption[]>([...props.definition.options])
const nextCursor = ref('')
const more = ref(props.definition.has_more_values)
const loading = ref(false)
const error = ref('')
let searchedQuery = ''
let sequence = 0
onBeforeUnmount(() => { sequence++ })
const limits = computed(() => ({ maxLength: props.definition.max_length, minValue: props.definition.min_value,
  maxValue: props.definition.max_value, valueType: props.definition.value_type }))
const enumeration = computed(() => props.definition.is_dictionary || props.definition.value_type === 'enum')
const allOptions = computed(() => {
  const result = new Map(options.value.map(option => [option.dictionary_value_id, option]))
  for (const row of props.modelValue) if (row.valueId && !result.has(String(row.valueId))) {
    result.set(String(row.valueId), {dictionary_value_id: String(row.valueId), value: row.value})
  }
  return [...result.values()]
})
async function search(append = false) {
  append = append && searchedQuery === query.value
  const requestedQuery = query.value
  const ticket = ++sequence
  loading.value = true; error.value = ''
  try {
    const result = await fetchOnlineAttributeValues(props.listingId, props.version, props.definition.id, requestedQuery, append ? nextCursor.value : '')
    if (ticket !== sequence) return
    if (result.version !== props.version || result.listing_id !== props.listingId || result.page.attribute_id !== props.definition.id) throw new Error('商品或属性已变化，请重新打开编辑器')
    options.value = append ? [...options.value, ...result.page.values] : result.page.values
    searchedQuery = requestedQuery
    nextCursor.value = result.page.next_cursor; more.value = result.page.has_more
  } catch (cause) { if (ticket === sequence) error.value = cause instanceof Error ? cause.message : '读取候选值失败' }
  finally { if (ticket === sequence) loading.value = false }
}
function patch(index: number, changes: Partial<OnlineAttributeRow>) {
  emit('update:modelValue', props.modelValue.map((row, i) => i === index ? {...row, ...changes} : row))
}
function choose(index: number, valueId: string) {
  const option = allOptions.value.find(item => item.dictionary_value_id === valueId)
  patch(index, {valueId: option ? valueId : undefined, value: option?.value || ''})
}
function add() {
  emit('update:modelValue', [...props.modelValue, {id: props.definition.id, parameterId: Number(props.definition.id), value: '',
    ...(props.definition.default_unit_id ? {unitId: Number(props.definition.default_unit_id)} : {})}])
}
const canAdd = computed(() => props.definition.is_collection && (!props.definition.max_value_count || props.modelValue.length < props.definition.max_value_count))
watch(() => props.disabled, disabled => {
  if (!disabled && enumeration.value && props.definition.has_more_values && !nextCursor.value) void search()
})
</script>

<template>
  <fieldset :disabled="disabled" class="mt-2 space-y-2">
    <div v-if="enumeration" class="flex gap-2">
      <input v-model="query" type="search" class="input min-w-0" :aria-label="`搜索 ${definition.name} 候选值`" placeholder="搜索平台候选值" @keydown.enter.prevent="search()" />
      <button type="button" class="btn btn-outline shrink-0" :disabled="loading" @click="search()">搜索</button>
    </div>
    <p v-if="error" role="alert" class="text-sm text-rose-600">{{ error }}<button type="button" class="ml-2 underline" @click="search()">重试</button></p>
    <p v-if="loading" role="status" class="muted">正在读取候选值…</p>
    <div v-for="(row,index) in modelValue" :key="index" class="flex flex-wrap items-center gap-2">
      <template v-if="enumeration">
        <select class="input min-w-0 flex-1" :aria-label="`${definition.name} 值 ${index + 1}`" :value="row.valueId ? String(row.valueId) : ''" @change="choose(index, ($event.target as HTMLSelectElement).value)">
          <option value="">{{ definition.allow_custom_values ? '自定义值或选择平台候选' : '请选择平台候选' }}</option>
          <option v-for="option in allOptions" :key="option.dictionary_value_id" :value="option.dictionary_value_id">{{ option.value }}</option>
        </select>
        <input v-if="definition.allow_custom_values && !row.valueId" class="input" :value="row.value" :aria-label="`${definition.name} 自定义值 ${index + 1}`" placeholder="填写自定义值" @input="patch(index, {value: ($event.target as HTMLInputElement).value})" />
      </template>
      <select v-else-if="definition.value_type === 'boolean'" class="input flex-1" :value="row.value" :aria-label="`${definition.name} 值 ${index + 1}`" @change="patch(index, {value: ($event.target as HTMLSelectElement).value})">
        <option value="">请选择</option><option value="true">是</option><option value="false">否</option>
      </select>
      <textarea v-else class="input min-w-0 flex-1" rows="1" :value="row.value" :aria-label="`${definition.name} 值 ${index + 1}`" :placeholder="definition.format_hint || '填写属性值'" @input="patch(index, {value: ($event.target as HTMLTextAreaElement).value})" />
      <select v-if="definition.unit_options.length" class="input w-auto" :value="String(row.unitId || '')" :aria-label="`${definition.name} 单位 ${index + 1}`" @change="patch(index, {unitId: Number(($event.target as HTMLSelectElement).value) || undefined})">
        <option value="">请选择单位</option><option v-for="unit in definition.unit_options" :key="unit.id" :value="unit.id">{{ unit.name }}</option>
      </select>
      <p v-if="attributeConstraintHint(limits, row.value)" class="basis-full text-xs text-slate-500">{{ attributeConstraintHint(limits, row.value) }}</p>
      <p v-if="attributeConstraintError(limits, row.value)" role="alert" class="basis-full text-sm text-rose-600">{{ attributeConstraintError(limits, row.value) }}</p>
      <button v-if="modelValue.length > 1" type="button" class="btn btn-outline" :aria-label="`移除 ${definition.name} 值 ${index + 1}`" @click="emit('update:modelValue', modelValue.filter((_, i) => i !== index))">移除</button>
    </div>
    <button v-if="enumeration && more" type="button" class="text-sm underline" :disabled="loading" @click="search(!!nextCursor)">加载更多候选值</button>
    <button v-if="canAdd" type="button" class="btn btn-outline" @click="add">添加一个值</button>
    <p v-if="definition.format_hint" class="muted">{{ definition.format_hint }}</p>
  </fieldset>
</template>
