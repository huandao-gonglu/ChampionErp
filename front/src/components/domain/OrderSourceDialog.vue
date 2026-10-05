<script setup lang="ts">
import { reactive, ref, useId, watch } from 'vue'
import WorkspaceDialog from '@/components/shared/WorkspaceDialog.vue'
import { procurementCommand } from '@/api/orders'
import type { OrderDetail, ProcurementLine, ProcurementSource } from '@/types/orders'
const props = defineProps<{ item: ProcurementLine; orderId: string }>()
const emit = defineEmits<{ close: []; updated: [detail: OrderDetail] }>()
const formId = useId()
const busy = ref(false)
const error = ref('')
const source = reactive<ProcurementSource>({
  supplier: '',
  source_platform: '',
  product_url: '',
  source_sku_id: '',
  specification: '',
  sku_url: '',
  sku_url_verified: false,
  ...props.item.selection.source,
})
// 直达链接或来源规格变化后，必须重新确认选中规格。
watch(
  () => [source.sku_url, source.source_sku_id, source.specification, source.product_url],
  () => {
    source.sku_url_verified = false
  }
)
async function save() {
  if (busy.value) return
  busy.value = true
  error.value = ''
  try {
    emit(
      'updated',
      await procurementCommand('select-source', {
        order_id: props.orderId,
        line_key: props.item.selection.line_key,
        revision: props.item.selection.revision,
        source: { ...source },
      })
    )
    emit('close')
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : '采购来源保存失败'
  } finally {
    busy.value = false
  }
}
</script>
<template>
  <WorkspaceDialog
    :open="true"
    title="采购来源"
    width="576px"
    class="order-ui"
    :close-disabled="busy"
    @close="$emit('close')"
  >
    <p class="order-muted mb-6">{{ item.line.title }} / {{ item.line.sku }}</p>
    <form :id="formId" class="order-form" @submit.prevent="save">
      <div class="order-form-columns">
        <label>采购平台<input
          v-model="source.source_platform"
          class="order-input"
          maxlength="100"
          placeholder="例如 1688"
        /></label><label>供应商<input v-model="source.supplier" class="order-input" maxlength="200" /></label>
      </div>
      <label>采购商品链接<input
        v-model="source.product_url"
        class="order-input"
        type="url"
        required
        maxlength="2000"
      /></label>
      <div class="order-form-columns">
        <label>来源 SKU<input
          v-model="source.source_sku_id"
          class="order-input"
          maxlength="200"
        /></label><label>采购规格<input
          v-model="source.specification"
          class="order-input"
          required
          maxlength="1000"
          placeholder="颜色、尺寸、款式等"
        /></label>
      </div>
      <label>规格直达链接（可选）<input
        v-model="source.sku_url"
        class="order-input"
        type="url"
        maxlength="2000"
        placeholder="填写已核对的规格链接"
      /></label>
      <label class="order-check"><input
        v-model="source.sku_url_verified"
        type="checkbox"
        :disabled="!source.sku_url || !source.source_sku_id"
      />已打开直达链接，确认会选中上述 SKU</label>
      <p class="order-muted">更换来源仅影响后续采购，历史记录保留原规格。</p>
      <p v-if="error" class="order-error" role="alert">{{ error }}</p>
    </form>
    <template #footer>
      <div class="order-footer">
        <button class="order-button" :disabled="busy" @click="$emit('close')">取消</button><button class="order-button order-primary" :form="formId" type="submit" :disabled="busy">
          {{ busy ? '保存中…' : '保存来源' }}
        </button>
      </div>
    </template>
  </WorkspaceDialog>
</template>
