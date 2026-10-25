<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { fetchOnlineAttributes, type OnlineAttributeDefinition, type OnlineAttributeRow, type OnlineListing } from '@/api/onlineProducts'
import OnlineAttributeField from './OnlineAttributeField.vue'

const props = defineProps<{listing: OnlineListing}>()
const emit = defineEmits<{change: [rows: OnlineAttributeRow[]]; validity: [valid: boolean]; names: [names: Record<string, string>]}>()
const definitions = ref<OnlineAttributeDefinition[]>([])
const selected = ref<string[]>([])
const values = ref<Record<string, OnlineAttributeRow[]>>({})
const query = ref('')
const loading = ref(false)
const error = ref('')
let sequence = 0
onBeforeUnmount(() => { sequence++ })
const original = computed(() => props.listing.content.attributes || [])
const writable = (attribute: OnlineAttributeDefinition) => !attribute.read_only && !attribute.managed_by
const missing = (id: string) => !original.value.some(row => String(row.id) === id && String(row.value ?? '').trim())
async function load() {
  const ticket = ++sequence
  loading.value = true; error.value = ''
  try {
    const attributes: OnlineAttributeDefinition[] = []
    const current: OnlineAttributeRow[] = []
    const cursors = new Set<string>()
    let cursor = '', category = ''
    do {
      const result = await fetchOnlineAttributes(props.listing.id, props.listing.version, cursor)
      if (ticket !== sequence) return
      if (result.listing_id !== props.listing.id || result.version !== props.listing.version || (category && category !== result.page.category_id)) throw new Error('商品类目已变化，请重新打开编辑器')
      category = result.page.category_id
      attributes.push(...result.page.attributes)
      current.push(...result.current_values)
      if (!result.page.has_more) break
      cursor = result.page.next_cursor
      if (!cursor || cursors.has(cursor)) throw new Error('属性分页不完整，请重新读取')
      cursors.add(cursor)
    } while (cursor)
    definitions.value = attributes
    for (const attribute of attributes) {
      if (values.value[attribute.id]) continue
      const rows = current.filter(row => row.id === attribute.id)
      values.value[attribute.id] = rows.length ? rows.map(row => ({id: attribute.id, parameterId: Number(attribute.id), value: String(row.value ?? ''),
        ...(row.valueId ? {valueId: row.valueId} : {}), ...(row.unitId ? {unitId: row.unitId} : {})}))
        : [{id: attribute.id, parameterId: Number(attribute.id), value: '', ...(attribute.default_unit_id ? {unitId: Number(attribute.default_unit_id)} : {})}]
    }
    emit('names', Object.fromEntries(attributes.map(attribute => [attribute.id, attribute.name])))
  } catch (cause) { if (ticket === sequence) error.value = cause instanceof Error ? cause.message : '读取类目属性失败' }
  finally { if (ticket === sequence) loading.value = false }
}
watch(() => [props.listing.id, props.listing.version], () => {
  definitions.value = []; selected.value = []; values.value = {}; void load()
}, {immediate: true})
const visible = computed(() => definitions.value.filter(attribute => `${attribute.name} ${attribute.id}`.toLowerCase().includes(query.value.trim().toLowerCase()))
  .sort((a, b) => Number(b.required && missing(b.id)) - Number(a.required && missing(a.id))))
const invalid = computed(() => selected.value.find(id => {
  const definition = definitions.value.find(item => item.id === id)
  return !definition || !writable(definition) || !values.value[id]?.length || values.value[id]!.some(row =>
    !row.value.trim() || ((definition.is_dictionary || definition.value_type === 'enum') && !definition.allow_custom_values && !row.valueId)
    || (definition.unit_options.length > 0 && !row.unitId))
}))
watch([selected, values, loading, error, invalid], () => {
  emit('change', selected.value.flatMap(id => values.value[id] || []))
  emit('validity', !selected.value.length || (!loading.value && !error.value && !invalid.value))
}, {deep: true, immediate: true})
</script>

<template>
  <section aria-label="Yandex 商品属性">
    <h3 class="font-medium">商品属性</h3>
    <p class="muted mt-2">勾选要修改或补充的字段。必填缺失项优先显示；各规格按实际尺码填写。</p>
    <p v-if="loading" role="status" class="muted mt-3">正在读取类目字段…</p>
    <p v-if="error" role="alert" class="mt-3 text-rose-600">{{ error }} <button type="button" class="underline" @click="load">重新读取</button></p>
    <input v-if="definitions.length" v-model="query" type="search" class="input mt-3" aria-label="搜索商品属性" placeholder="搜索属性名称或编号" />
    <p v-if="invalid" role="alert" class="mt-2 text-rose-600">请完整填写已勾选的属性值和单位。</p>
    <div class="mt-4 grid gap-4 sm:grid-cols-2">
      <div v-for="attribute in visible" :key="attribute.id" class="rounded-lg border border-accent-200 p-3 dark:border-dark-700" :data-attribute-id="attribute.id">
        <label class="flex items-center gap-2"><input v-model="selected" type="checkbox" :value="attribute.id" :disabled="!writable(attribute) || loading" :aria-label="`修改 ${attribute.name}`" /><span>{{ attribute.name }}</span><span v-if="attribute.required" class="text-xs text-rose-600">{{ missing(attribute.id) ? '必填 · 未填写' : '必填' }}</span></label>
        <p class="muted mt-1 text-xs">属性编号：{{ attribute.id }}</p>
        <OnlineAttributeField v-if="values[attribute.id]" v-model="values[attribute.id]!" :listing-id="listing.id" :version="listing.version" :definition="attribute" :disabled="!selected.includes(attribute.id) || loading" />
        <p v-if="!writable(attribute)" class="muted mt-1">此属性仅供查看。</p>
      </div>
    </div>
  </section>
</template>
