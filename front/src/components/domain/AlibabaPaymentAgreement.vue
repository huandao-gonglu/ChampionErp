<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { selfPurchasePaymentStatus, type PaymentAgreement } from '@/api/alibabaSelfPurchase'

const props = defineProps<{ channel: string; disabled?: boolean }>()
const emit = defineEmits<{ ready: [value: boolean] }>()
const agreement = ref<PaymentAgreement | null>(null)
const busy = ref(false), error = ref('')
const label = computed(() => props.channel === 'alipay' ? '支付宝' : '先采后付')
let revision = 0
async function refresh() {
  if (props.disabled) return
  const ticket = ++revision, channel = props.channel
  busy.value = true; error.value = ''; agreement.value = null; emit('ready', false)
  try {
    const value = await selfPurchasePaymentStatus(channel)
    if (ticket !== revision) return
    if (value.channel !== channel) throw new Error('支付渠道签约状态不一致，请重新查询')
    agreement.value = value; emit('ready', value.ready)
  }
  catch (exc) { if (ticket === revision) error.value = exc instanceof Error ? exc.message : '免密签约状态查询失败' }
  finally { if (ticket === revision) busy.value = false }
}
watch(() => props.channel, () => {
  revision++; agreement.value = null; error.value = ''; busy.value = false; emit('ready', false)
  void refresh()
}, { immediate: true })
watch(() => props.disabled, value => { if (!value && !agreement.value && !error.value) void refresh() })
onBeforeUnmount(() => { revision++ })
</script>

<template>
  <div class="space-y-2 text-sm" :aria-label="`${label}免密签约`">
    <p v-if="busy" role="status">正在查询{{ label }}免密签约状态…</p>
    <p v-if="error" role="alert" class="text-red-600">{{ error }}</p>
    <p v-if="agreement" role="status">{{ agreement.message }}</p>
    <div class="flex flex-wrap gap-3">
      <a v-if="agreement && !agreement.ready && agreement.sign_url" :href="agreement.sign_url" target="_blank" rel="noopener noreferrer" class="btn btn-outline">前往 1688 开通{{ label }}免密</a>
      <button type="button" class="btn btn-outline" :disabled="busy || disabled" @click="refresh">刷新免密签约状态</button>
    </div>
  </div>
</template>
