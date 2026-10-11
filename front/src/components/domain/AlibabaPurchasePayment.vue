<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import AlibabaPaymentAgreement from './AlibabaPaymentAgreement.vue'
import type { SelfPurchaseRecord } from '@/api/alibabaSelfPurchase'

const props = defineProps<{ record: SelfPurchaseRecord; disabled: boolean }>()
const emit = defineEmits<{ pay: [] }>()
const confirmed = ref(false), ready = ref(false)
const label = computed(() => props.record.pay_channel === 'alipay' ? '支付宝' : '先采后付')
watch(() => props.record, () => { confirmed.value = false })
watch(() => props.record.pay_channel, () => { ready.value = false })
</script>

<template>
  <div v-if="record.payment" class="space-y-2 text-sm" aria-label="支付结果">
    <p role="status">{{ record.payment.message }}</p>
  </div>
  <div v-else-if="record.state === 'created' && ['alipay','shegou'].includes(record.pay_channel) && record.purchase_record_id" class="space-y-3">
    <AlibabaPaymentAgreement :channel="record.pay_channel" :disabled="disabled" @ready="ready = $event" />
    <label class="flex gap-2 text-sm"><input v-model="confirmed" type="checkbox" :disabled="disabled || !ready" :aria-label="`确认${label}支付`" />确认使用{{ label }}支付 ¥{{ (record.preview.total_fen / 100).toFixed(2) }}{{ record.pay_channel === 'alipay' ? '，通过支付宝免密直接扣款' : '' }}。</label>
    <button class="btn btn-primary" :disabled="disabled || !ready || !confirmed" @click="confirmed = false; emit('pay')">确认{{ label }}支付</button>
  </div>
</template>
