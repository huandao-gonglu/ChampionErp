<script setup lang="ts">
import { useOrderNotificationsStore } from '@/stores/orderNotifications'
import { orderPlatformNames, orderStateNames } from '@/types/orders'
const store = useOrderNotificationsStore()
</script>
<template>
  <section
    class="rounded-lg border border-accent-200 bg-white p-5 dark:border-dark-700 dark:bg-dark-900"
    aria-label="订单摘要"
  >
    <div class="flex items-center justify-between">
      <h2 class="card-title">订单摘要</h2>
      <RouterLink class="btn btn-outline" to="/?tab=orders">查看全部订单</RouterLink>
    </div>
    <p v-if="store.summaryError" role="alert" class="mt-3 text-red-600">{{ store.summaryError }}</p>
    <div class="my-4 flex flex-wrap gap-3">
      <span class="badge-info">{{ store.pendingCount }} 个待发货</span><span class="badge-muted">{{ store.summary.unread }} 条未读提醒</span><RouterLink v-if="store.summary.attention_count" to="/?tab=orders" class="text-amber-700">
        {{ store.summary.attention_count }} 条同步异常
      </RouterLink>
    </div>
    <ul class="divide-y divide-accent-100 dark:divide-dark-800">
      <li v-for="order in store.summary.recent" :key="order.id" class="py-3">
        <RouterLink
          class="flex flex-wrap items-center justify-between gap-2"
          :to="{ path: '/', query: { tab: 'orders', order: order.id } }"
        >
          <span>{{ orderPlatformNames[order.platform] }} · {{ order.order_id
          }}<span class="muted ml-2">{{ order.title }}</span></span><span class="badge-muted">{{ orderStateNames[order.state] }}</span>
        </RouterLink>
      </li>
    </ul>
    <p v-if="!store.summary.recent.length" class="muted">暂无订单。可在订单中心同步平台订单。</p>
  </section>
</template>
