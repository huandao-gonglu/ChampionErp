<script setup lang="ts">
import type { OrderAmount, OrderPlatform } from '@/types/orders'

defineProps<{ value: OrderAmount; platform: OrderPlatform }>()
</script>

<template>
  <div class="mt-2">
    <template v-if="value.amount !== ''">
      <p class="text-xs text-slate-500 dark:text-slate-400">
        {{
          value.amount_breakdown
            ? '商品金额（含平台补贴）'
            : platform === 'yandex'
              ? '付款金额（待同步明细）'
              : '订单金额'
        }}
      </p>
      <p class="font-semibold tabular-nums">{{ value.amount }} {{ value.currency }}</p>
      <template v-if="value.amount_breakdown">
        <p class="muted mt-1">
          付款 {{ value.amount_breakdown.payment }} {{ value.currency }} ｜平台补贴
          {{ value.amount_breakdown.subsidy }} {{ value.currency }}
          <span v-if="Number(value.amount_breakdown.cashback) !== 0">
            ｜积分抵扣 {{ value.amount_breakdown.cashback }} {{ value.currency }}
          </span>
        </p>
        <p class="muted">未扣除佣金、物流等费用</p>
      </template>
    </template>
    <p v-else class="muted">金额待确认</p>
  </div>
</template>
