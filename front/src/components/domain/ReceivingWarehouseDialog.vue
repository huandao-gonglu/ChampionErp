<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import WorkspaceDialog from '@/components/shared/WorkspaceDialog.vue'
import { busCommand } from '@/api/fulfillment'
import { fetchReceivingWarehouse, saveReceivingWarehouse } from '@/api/receivingWarehouse'
import type { ReceivingWarehouseView } from '@/api/receivingWarehouse'
const props = defineProps<{ value: ReceivingWarehouseView; lineKey?: string }>()
const emit = defineEmits<{ close: []; saved: [ReceivingWarehouseView] }>()
const view = ref(props.value)
const selection = ref(props.value.warehouse?.address_id ? `${props.value.warehouse.address_id}:${props.value.warehouse.section_id}` : '')
const selected = computed(() => view.value.candidates.find(a => `${a.id}:${a.section_id}` === selection.value))
const error = ref(''), loading = ref(true), saving = ref(false)
const confirmed = ref(false), existingConfirmed = ref(false)
let version = 0
async function load(refreshCatalog = false) {
  const request = ++version
  loading.value = true; error.value = ''; confirmed.value = false; existingConfirmed.value = false
  try {
    if (refreshCatalog) await busCommand('catalog')
    const result = await fetchReceivingWarehouse(props.value.order_id)
    if (request !== version) return
    view.value = result
    if (!selected.value) selection.value = ''
  } catch (exc) { if (request === version) error.value = exc instanceof Error ? exc.message : '读取收货地址失败' }
  finally { if (request === version) loading.value = false }
}
watch(selection, () => { confirmed.value = false; existingConfirmed.value = false })
const canSave = computed(() => !error.value && !loading.value && !saving.value && view.value.can_confirm && !!view.value.carrier && !!selected.value && confirmed.value && (!view.value.has_purchases || !!view.value.warehouse || existingConfirmed.value))
async function save() {
  if (!canSave.value || !selected.value || !view.value.carrier) return
  saving.value = true; error.value = ''
  try {
    emit('saved', await saveReceivingWarehouse({ order_id: view.value.order_id, revision: view.value.revision,
      address_id: selected.value.id, address_revision: selected.value.revision, section_id: selected.value.section_id,
      delivery_key: view.value.delivery_key, confirmed: confirmed.value, existing_purchase_confirmed: existingConfirmed.value }))
  } catch (exc) { error.value = exc instanceof Error ? exc.message : '保存收货地址失败' }
  finally { saving.value = false }
}
onMounted(() => load(true))
onBeforeUnmount(() => { version++ })
</script>
<template>
  <WorkspaceDialog open title="选择采购收货地址" width="740px" class="order-ui" :close-disabled="saving" @close="emit('close')">
    <form id="receiving-warehouse" class="order-form" @submit.prevent="save">
      <p v-if="error" role="alert" class="order-error">{{ error }}</p>
      <p v-if="loading" role="status">正在读取可用收货地址…</p>
      <div class="flex items-center gap-3"><a class="order-link" href="/auth?auth_section=crossborderbus&bus_section=rules" target="_blank" rel="noopener noreferrer">管理物流设置</a><button type="button" class="order-button" :disabled="loading || saving" @click="load(true)">刷新地址</button></div>
      <fieldset :disabled="loading || saving" class="order-form">
        <p>平台物流商：{{ view.delivery_name || '待同步' }}</p>
        <p v-if="view.delivery_evidence.length" class="order-muted">平台交货信息：{{ view.delivery_evidence.join('；') }}</p>
        <p v-if="!view.carrier" role="status" class="order-muted">尚未识别平台物流商，请先同步平台交货信息。</p>
        <label>采购收货地址<select v-model="selection" aria-label="采购收货地址" class="order-input" required><option value="" disabled>请选择收货地址</option><option v-for="item in view.candidates" :key="`${item.id}:${item.section_id}`" :value="`${item.id}:${item.section_id}`" :disabled="view.locked && !!view.warehouse?.address_id && item.id !== view.warehouse.address_id">{{ item.warehouse_name }} · {{ item.name }}{{ item.is_default ? '（默认）' : '' }}</option></select></label>
        <p v-if="!view.candidates.length" class="order-muted">暂无符合本单物流方案的收货地址，请在物流设置中添加地址并确认承接范围后刷新。</p>
        <div v-if="selected" class="order-form rounded-lg border border-default p-3">
          <strong>{{ selected.name }}</strong><p>{{ selected.address.fullName }} · {{ selected.address.mobile || selected.address.phone }}</p>
          <p>{{ [selected.address.provinceText, selected.address.cityText, selected.address.areaText, selected.address.townText, selected.address.address].filter(Boolean).join(' ') }}</p>
          <p v-if="selected.remark" class="order-muted">{{ selected.remark }}</p>
          <p v-if="selected.frozen" class="order-muted">显示采购时保存的收货地址。</p>
        </div>
        <p v-if="view.locked" class="order-muted">已有采购使用此仓库和地址，不能换仓；可重新确认物流承接范围。</p>
        <label v-if="view.has_purchases && !view.warehouse" class="order-check"><input v-model="existingConfirmed" type="checkbox" />已核对已有采购包裹实际寄往此仓库和地址</label>
        <label class="order-check"><input v-model="confirmed" type="checkbox" />已核对本单物流方案、收件人、电话及地址</label>
      </fieldset>
    </form>
    <template #footer><div class="order-footer"><button type="button" class="order-button" :disabled="saving" @click="emit('close')">取消</button><button form="receiving-warehouse" class="order-button order-primary" :disabled="!canSave">保存收货仓</button></div></template>
  </WorkspaceDialog>
</template>
