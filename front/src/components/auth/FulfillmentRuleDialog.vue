<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import WorkspaceDialog from '@/components/shared/WorkspaceDialog.vue'
import FulfillmentPlanFields from '@/components/domain/FulfillmentPlanFields.vue'
import { saveFulfillmentRule } from '@/api/fulfillment'
import { orderPlatformNames } from '@/types/orders'
import type { BusSettings, DeliveryChoice, FulfillmentPlan, FulfillmentRule } from '@/types/fulfillment'
const props = defineProps<{ settings: BusSettings; rule?: FulfillmentRule }>()
const emit = defineEmits<{ close: []; saved: [BusSettings] }>()
const sourceIndex = ref(0)
const choices = computed(() => props.settings.delivery_choices)
const initial = props.rule
const source = computed<DeliveryChoice | undefined>(() => initial || choices.value[sourceIndex.value])
const plan = ref<FulfillmentPlan>({ section_id: initial?.section_id || 0, warehouse_id: initial?.warehouse_id || 0, service_ids: [...(initial?.service_ids || [])] })
const country = ref(initial?.country || source.value?.country || '')
const compatible = ref<number[]>([...(initial?.compatible_warehouse_ids || [])])
const confirmed = ref(false)
const autoSubmit = ref(initial?.auto_submit ?? true)
const ready = ref(false)
const busy = ref(false)
const error = ref('')
const section = computed(() => props.settings.catalog.sections?.find(s => s.section_id === plan.value.section_id))
watch(sourceIndex, () => { country.value = source.value?.country || ''; confirmed.value = false })
watch(() => plan.value.section_id, () => { compatible.value = []; confirmed.value = false })
watch(() => plan.value.warehouse_id, id => { confirmed.value = false; if (id && !compatible.value.includes(id)) compatible.value.push(id) })
function toggleWarehouse(id: number, checked: boolean) {
  compatible.value = checked ? [...new Set([...compatible.value, id])] : compatible.value.filter(v => v !== id)
  confirmed.value = false
}
async function save() {
  if (!source.value || !ready.value || !confirmed.value || busy.value) return
  busy.value = true
  error.value = ''
  try {
    emit('saved', await saveFulfillmentRule({ ...source.value, ...plan.value, id: initial?.id || '', country: country.value.trim().toUpperCase(), compatible_warehouse_ids: compatible.value, confirmed: true, auto_submit: autoSubmit.value }))
  } catch (exc) { error.value = exc instanceof Error ? exc.message : '方案保存失败' }
  finally { busy.value = false }
}
</script>
<template>
  <WorkspaceDialog :open="true" title="配置默认履约方案" width="780px" class="order-ui" :close-disabled="busy" @close="emit('close')">
    <form id="fulfillment-rule-form" @submit.prevent="save">
      <fieldset :disabled="busy" class="order-form">
        <p v-if="error" role="alert" class="order-error">{{ error }}</p>
        <label v-if="!initial">平台配送来源
          <select v-model="sourceIndex" class="order-input">
            <option v-for="(choice, index) in choices" :key="index" :value="index">{{ orderPlatformNames[choice.platform] }} / {{ choice.account_id }} / {{ choice.fulfillment }} · {{ choice.delivery_method_name }}</option>
          </select>
        </label>
        <div v-if="source" class="source-card">
          <h4>平台配送条件</h4>
          <p>{{ orderPlatformNames[source.platform] }} / {{ source.account_id }} / {{ source.fulfillment }}</p>
          <p>平台仓：{{ source.platform_warehouse_name || source.platform_warehouse_id }} · 配送方式：{{ source.delivery_method_name }}</p>
          <p class="order-muted">来源于平台订单；新订单按实际配送信息匹配。</p>
        </div>
        <p v-else class="order-muted">还没有可用的配送来源，请先同步平台订单。</p>
        <label>目的国代码<input v-model="country" class="order-input" placeholder="例如 RU" pattern="[A-Za-z]{2}" maxlength="2" required /><span class="order-muted">平台未返回目的国时，需按实际订单确认，不能仅凭渠道名称推断。</span></label>
        <FulfillmentPlanFields v-model="plan" :disabled="busy" :sections="settings.catalog.sections || []" @ready="ready = $event" />
        <fieldset v-if="section">
          <legend class="mb-3">允许本单覆盖的兼容仓库</legend>
          <label v-for="w in section.storehouse_list" :key="w.id" class="order-check mb-2"><input type="checkbox" :checked="compatible.includes(w.id)" :disabled="w.id === plan.warehouse_id" @change="toggleWarehouse(w.id, ($event.target as HTMLInputElement).checked)" />{{ w.name }}{{ w.id === plan.warehouse_id ? '（默认）' : '' }}</label>
        </fieldset>
        <label class="order-check"><input v-model="confirmed" type="checkbox" />已向合作仓确认上述配送方式、目的国和所选仓库的承接范围</label>
        <label class="order-check"><input v-model="autoSubmit" type="checkbox" />匹配订单资料齐备后自动预报</label>
        <p class="order-muted">正式预报将按合作渠道、仓库和服务扣费。</p>
      </fieldset>
    </form>
    <template #footer><div class="order-footer"><button class="order-button" :disabled="busy" @click="emit('close')">取消</button><button form="fulfillment-rule-form" class="order-button order-primary" :disabled="busy || !source || !ready || !confirmed || !compatible.includes(plan.warehouse_id)">{{ busy ? '保存中…' : '保存方案' }}</button></div></template>
  </WorkspaceDialog>
</template>
<style scoped>
.source-card { padding: 16px; border-radius: 6px; background: var(--order-bg); }
.source-card h4 { font-weight: 600; margin-bottom: 10px; }
.source-card p + p { margin-top: 8px; }
</style>
