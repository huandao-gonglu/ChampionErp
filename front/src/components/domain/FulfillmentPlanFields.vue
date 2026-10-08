<script setup lang="ts">
import { computed, ref, useId, watch } from 'vue'
import { fetchBusServices } from '@/api/fulfillment'
import type { BusSection, BusServices, FulfillmentPlan } from '@/types/fulfillment'
const props = defineProps<{ modelValue: FulfillmentPlan; sections: BusSection[]; disabled?: boolean; sectionLocked?: boolean; compatibleWarehouseIds?: number[]; warehouseLocked?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [FulfillmentPlan]; ready: [boolean] }>()
const serviceGroup = useId()
const services = ref<BusServices>({ core_data: [], optional_data: [] })
const error = ref('')
const loading = ref(false)
const section = computed(() => props.sections.find(s => s.section_id === props.modelValue.section_id))
const warehouses = computed(() => section.value?.storehouse_list.filter(w => !props.compatibleWarehouseIds || props.compatibleWarehouseIds.includes(w.id)) || [])
let version = 0
function change(sectionId: number, warehouseId: number) {
  emit('update:modelValue', { section_id: sectionId, warehouse_id: warehouseId, service_ids: [] })
}
function toggle(id: number, checked: boolean) {
  emit('update:modelValue', { ...props.modelValue, service_ids: checked ? [...new Set([...props.modelValue.service_ids, id])] : props.modelValue.service_ids.filter(s => s !== id) })
}
const selectionValid = computed(() => {
  if (loading.value || error.value || !warehouses.value.some(w => w.id === props.modelValue.warehouse_id)) return false
  const core = services.value.core_data.filter(s => props.modelValue.service_ids.includes(s.id))
  return !services.value.core_data.length || core.length === 1
})
watch(selectionValid, value => emit('ready', value))
function chooseCore(id: number) {
  const core = new Set(services.value.core_data.map(s => s.id))
  emit('update:modelValue', { ...props.modelValue, service_ids: [...props.modelValue.service_ids.filter(item => !core.has(item)), id] })
}
watch([() => props.modelValue.section_id, () => props.modelValue.warehouse_id], async ([sectionId, warehouseId]) => {
  const current = ++version
  error.value = ''
  emit('ready', false)
  if (!sectionId || !warehouseId) { services.value = { core_data: [], optional_data: [] }; loading.value = false; return }
  loading.value = true
  try {
    const value = await fetchBusServices(sectionId!, warehouseId!)
    if (current !== version) return
    services.value = value
    const allowed = new Set([...value.core_data, ...value.optional_data].map(s => s.id))
    const selected = [...new Set(props.modelValue.service_ids.filter(id => allowed.has(id)))]
    if (value.core_data.length === 1 && !selected.includes(value.core_data[0]!.id)) selected.push(value.core_data[0]!.id)
    emit('update:modelValue', { ...props.modelValue, service_ids: selected })
    // 只有基础服务选择有效时才允许保存。
  } catch (exc) {
    if (current === version) error.value = exc instanceof Error ? exc.message : '服务读取失败'
  } finally {
    if (current === version) loading.value = false
  }
}, { immediate: true })
</script>
<template>
  <label v-if="!sectionLocked">跨境巴士合作渠道
    <select :disabled="disabled || loading" class="order-input" :value="modelValue.section_id" @change="change(Number(($event.target as HTMLSelectElement).value), 0)">
      <option :value="0">请选择已合作渠道</option>
      <option v-for="s in sections" :key="s.section_id" :value="s.section_id">{{ s.section_name }}</option>
    </select>
  </label>
  <p v-else class="order-muted">合作渠道：{{ section?.section_name || '当前渠道不可用' }}</p>
  <label>收货仓库
    <select :disabled="disabled || loading || warehouseLocked" class="order-input" :value="modelValue.warehouse_id" @change="change(modelValue.section_id, Number(($event.target as HTMLSelectElement).value))">
      <option :value="0">请选择收货仓库</option>
      <option v-for="w in warehouses" :key="w.id" :value="w.id">{{ w.name }}</option>
    </select>
  </label>
  <fieldset>
    <legend class="mb-3">增值服务</legend>
    <p v-if="loading" class="order-muted" role="status">正在读取该仓可用服务…</p>
    <p v-if="error" class="order-error" role="alert">{{ error }}</p>
    <p v-if="services.core_data.length" class="order-muted mb-2">基础服务（选一项）</p>
    <label v-for="s in services.core_data" :key="`core-${s.id}`" class="order-check mb-2"><input type="radio" :name="serviceGroup" :disabled="disabled || loading" :checked="modelValue.service_ids.includes(s.id)" @change="chooseCore(s.id)" />{{ s.name }} <span class="order-muted">¥{{ s.gold }}</span></label>
    <p v-if="services.optional_data.length" class="order-muted mb-2">附加服务（可多选）</p>
    <label v-for="s in services.optional_data" :key="s.id" class="order-check mb-2"><input type="checkbox" :disabled="disabled || loading" :checked="modelValue.service_ids.includes(s.id)" @change="toggle(s.id, ($event.target as HTMLInputElement).checked)" />{{ s.name }} <span class="order-muted">¥{{ s.gold }}</span></label>
    <p v-if="!loading && !error && modelValue.warehouse_id && !services.core_data.length && !services.optional_data.length" class="order-muted">该仓未提供增值服务。</p>
  </fieldset>
</template>
