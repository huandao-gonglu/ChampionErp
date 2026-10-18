<script setup lang="ts">
import { ref } from 'vue'
import WorkspaceDialog from '@/components/shared/WorkspaceDialog.vue'
import { procurementCommand } from '@/api/orders'
import type { OrderDetail } from '@/types/orders'
const props = defineProps<{ order: OrderDetail }>()
const emit = defineEmits<{ close: []; updated: [detail: OrderDetail] }>()
const platform = ref('1688')
const number = ref('')
const note = ref('')
const selected = ref<Record<string, boolean>>({})
const quantities = ref(Object.fromEntries(props.order.lines.map(l => [l.selection.line_key, Math.max(1, l.remaining_quantity)])))
const busy = ref(false)
const error = ref('')
let previous = { fingerprint: '', id: '' }
async function save() {
  if (busy.value) return
  const items = props.order.lines.filter(l => selected.value[l.selection.line_key]).map(l => ({
    line_key: l.selection.line_key, quantity: quantities.value[l.selection.line_key], title: l.line.title,
    product_id: l.selection.source?.source_offer_id || '', sku: l.selection.source?.source_sku_id || '',
    source: l.selection.status === 'confirmed' ? l.selection.source : null, image_url: l.line.image_url,
  }))
  if (!number.value.trim() || items.some(i => !Number.isInteger(i.quantity) || (i.quantity || 0) < 1)) {
    error.value = '请填写采购单号，并核对已选商品的采购数量。'; return
  }
  const body = { order_id: props.order.order.id, source_platform: platform.value,
    purchase_order_number: number.value.trim(), items, note: note.value }
  const fingerprint = JSON.stringify(body)
  if (previous.fingerprint !== fingerprint) previous = { fingerprint, id: crypto.randomUUID() }
  busy.value = true; error.value = ''
  try {
    emit('updated', await procurementCommand('record-purchase', { ...body, request_id: previous.id }))
    emit('close')
  } catch (exc) { error.value = exc instanceof Error ? exc.message : '采购登记失败' }
  finally { busy.value = false }
}
</script>
<template>
  <WorkspaceDialog :open="true" title="登记采购单" width="620px" class="order-ui" :close-disabled="busy" @close="emit('close')">
    <form id="order-purchase" class="order-form" @submit.prevent="save">
      <fieldset class="order-form" :disabled="busy">
        <label>采购平台<select v-model="platform" class="order-input"><option value="1688">1688</option><option value="taobao">淘宝</option><option value="pinduoduo">拼多多</option><option value="other">其他</option></select></label>
        <label>采购单号<input v-model="number" class="order-input" maxlength="200" required /></label>
        <label>备注（选填）<textarea v-model="note" class="order-input" maxlength="2000" /></label>
        <details>
          <summary>关联销售商品（选填）</summary><p class="order-muted mt-2">可登记多个商品和规格，追加采购无需作废原记录。</p>
          <div v-for="line in order.lines" :key="line.selection.line_key" class="mt-3">
            <label class="order-check"><input v-model="selected[line.selection.line_key]" type="checkbox" />{{ line.line.title }} · {{ line.line.sku }}</label>
            <label v-if="selected[line.selection.line_key]">采购数量<input v-model.number="quantities[line.selection.line_key]" class="order-input" type="number" min="1" step="1" /></label>
          </div>
        </details>
        <p class="order-muted">登记已实际发生的采购；1688 采购单保存后会刷新一次商品和物流信息。</p>
        <p v-if="error" class="order-error" role="alert">{{ error }}</p>
      </fieldset>
    </form>
    <template #footer><div class="order-footer"><button class="order-button" :disabled="busy" @click="emit('close')">取消</button><button form="order-purchase" class="order-button order-primary" :disabled="busy">{{ busy ? '保存中…' : '保存采购单' }}</button></div></template>
  </WorkspaceDialog>
</template>
