<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { fetchOrderDetail } from '@/api/orders'
import type { OrderDetail } from '@/types/orders'
import { orderPlatformNames, orderStateNames } from '@/types/orders'
import OrderAmountDetails from './OrderAmountDetails.vue'
import OrderProcurementLine from './OrderProcurementLine.vue'
const props = defineProps<{ orderId: string }>()
defineEmits<{ back: [] }>()
const detail = ref<OrderDetail | null>(null)
const error = ref('')
const busy = ref(false)
async function load() {
  busy.value = true
  try {
    detail.value = await fetchOrderDetail(props.orderId)
    error.value = ''
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : '订单详情读取失败'
  } finally {
    busy.value = false
  }
}
onMounted(load)
</script>
<template>
  <section class="space-y-5" aria-label="订单详情">
    <div class="flex items-center justify-between">
      <button class="btn btn-outline" @click="$emit('back')">返回订单中心</button><button class="btn btn-outline" :disabled="busy" @click="load">刷新订单详情</button>
    </div>
    <p v-if="error" role="alert" class="text-red-600">{{ error }}</p>
    <p v-if="busy" role="status">正在读取订单…</p>
    <template v-if="detail">
      <header
        class="rounded-lg border border-accent-200 bg-white p-5 dark:border-dark-700 dark:bg-dark-900"
      >
        <h2 class="card-title">
          {{ orderPlatformNames[detail.order.platform] }} · {{ detail.order.order_id }}
        </h2>
        <p class="mt-2">
          {{ orderStateNames[detail.order.state] }} · {{ detail.order.fulfillment }}
        </p>
        <OrderAmountDetails :value="detail.order" :platform="detail.order.platform" />
        <p class="muted mt-2">
          采购记录只反映内部处理进度，不会自动修改平台订单状态或向采购平台下单。
        </p>
      </header>
      <OrderProcurementLine
        v-for="(line, index) in detail.lines"
        :key="`${line.selection.line_key}:${index}:${line.selection.revision}`"
        :item="line"
        :order="detail.order"
        @updated="detail = $event"
      />
    </template>
  </section>
</template>
