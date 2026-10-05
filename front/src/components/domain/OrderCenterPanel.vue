<script setup lang="ts">
import { onMounted, onBeforeUnmount } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useOrderNotificationsStore } from '@/stores/orderNotifications'
import { orderPlatformNames, orderStateNames } from '@/types/orders'
import OrderDetailPanel from './OrderDetailPanel.vue'
const store = useOrderNotificationsStore()
const route = useRoute()
const router = useRouter()
const processingNames = {
  queued: '等待处理',
  running: '处理中',
  retry: '等待重试',
  failed: '需要处理',
  done: '已完成',
}
const procurementNames: Record<string, string> = {
  unpurchased: '待采购',
  partial: '部分采购',
  purchased: '已采购',
  unknown: '待核对',
}
function date(value: string) {
  return value ? new Date(value).toLocaleString('zh-CN') : '未提供'
}
function search() {
  void store.filter(store.platform, store.state)
}
onMounted(() => {
  store.listActive = true
  void store.refresh()
})
onBeforeUnmount(() => {
  store.listActive = false
})
</script>
<template>
  <OrderDetailPanel
    v-if="route.query.order"
    :key="String(route.query.order)"
    :order-id="String(route.query.order)"
    @back="router.push({ query: { tab: 'orders' } })"
  />
  <section
    v-else
    class="rounded-lg border border-accent-200 bg-white p-5 dark:border-dark-700 dark:bg-dark-900"
    aria-label="订单中心"
  >
    <div class="flex flex-wrap items-center justify-between gap-3">
      <div>
        <h2 class="card-title">订单中心</h2>
        <p class="muted mt-1">
          查看三平台订单，核对采购规格并记录实际采购；平台履约状态与采购进度分别展示。
        </p>
      </div>
      <div class="flex flex-wrap gap-2">
        <button
          class="btn btn-outline"
          :disabled="store.busy"
          @click="store.command('sync', { platform: store.platform })"
        >
          立即同步平台订单
        </button>
        <button class="btn btn-outline" @click="store.refresh">刷新列表</button>
        <button
          class="btn btn-outline"
          :disabled="store.desktopEnabled"
          @click="store.enableDesktop"
        >
          {{ store.desktopEnabled ? '桌面提醒已开启' : '开启桌面提醒' }}
        </button>
      </div>
    </div>
    <p v-if="store.error" role="alert" class="mt-3 text-sm text-red-600">
      {{ store.error }}（保留上次成功读取的数据）
    </p>
    <form class="mt-4 flex gap-2" @submit.prevent="search">
      <label class="flex-1">搜索订单号、销售 SKU 或商品<input
        v-model="store.query"
        class="input w-full"
        type="search"
      /></label><button class="btn btn-outline self-end">搜索</button>
    </form>
    <div class="mt-4 flex flex-wrap items-center gap-3">
      <label>平台
        <select
          class="input"
          :value="store.platform"
          @change="store.filter(($event.target as HTMLSelectElement).value, store.state)"
        >
          <option value="">全部平台</option>
          <option v-for="(label, key) in orderPlatformNames" :key="key" :value="key">
            {{ label }}
          </option>
        </select></label>
      <label>状态
        <select
          class="input"
          :value="store.state"
          @change="store.filter(store.platform, ($event.target as HTMLSelectElement).value)"
        >
          <option value="">全部状态</option>
          <option v-for="(label, key) in orderStateNames" :key="key" :value="key">
            {{ label }}
          </option>
        </select></label>
      <span class="badge-info">{{ store.page.counts.pending_shipment || 0 }} 个待发货</span>
      <span class="badge-muted">{{ store.summary.unread }} 条未读提醒</span>
      <button
        class="btn btn-outline"
        :disabled="store.busy || !store.summary.unread"
        @click="store.command('acknowledge', { through_id: store.summary.latest_alert_id })"
      >
        标记当前提醒已读
      </button>
    </div>
    <div class="mt-4 overflow-x-auto">
      <table class="w-full text-left text-sm">
        <thead>
          <tr>
            <th class="p-2">平台 / 履约</th>
            <th class="p-2">订单 / 商品</th>
            <th class="p-2">状态</th>
            <th class="p-2">采购进度 / 发货截止</th>
            <th class="p-2">最近核对</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="order in store.page.items"
            :key="order.id"
            class="border-t border-accent-200 dark:border-dark-700"
          >
            <td class="p-2">
              {{ orderPlatformNames[order.platform] }}
              <p class="muted">{{ order.fulfillment }}</p>
            </td>
            <td class="p-2">
              <p>{{ order.order_id }}</p>
              <p>{{ order.title || '暂无商品标题' }}</p>
              <p class="muted">{{ order.amount }} {{ order.currency }}</p>
              <RouterLink
                class="btn btn-outline mt-2"
                :to="{ path: '/', query: { tab: 'orders', order: order.id } }"
              >
                查看订单
              </RouterLink>
            </td>
            <td class="p-2">
              <span :class="order.state === 'pending_shipment' ? 'badge-info' : 'badge-muted'">{{
                orderStateNames[order.state]
              }}</span>
              <p class="muted mt-1">{{ order.status }} {{ order.shipping_status }}</p>
            </td>
            <td class="p-2">
              <p>{{ procurementNames[order.procurement_status || 'unknown'] }}</p>
              <p class="muted">{{ date(order.shipment_deadline || '') }}</p>
            </td>
            <td class="p-2">{{ date(order.checked_at) }}</td>
          </tr>
        </tbody>
      </table>
      <p v-if="!store.page.items.length" class="muted py-6 text-center">
        当前没有符合条件的订单。完成店铺授权后可点击“立即同步平台订单”。
      </p>
    </div>
    <div class="mt-3 flex items-center gap-3">
      <span class="muted">共 {{ store.page.total }} 个订单</span><button
        class="btn btn-outline"
        :disabled="store.offset === 0"
        @click="store.filter(store.platform, store.state, Math.max(0, store.offset - 50))"
      >
        上一页
      </button><button
        class="btn btn-outline"
        :disabled="store.offset + 50 >= store.page.total"
        @click="store.filter(store.platform, store.state, store.offset + 50)"
      >
        下一页
      </button>
    </div>
    <details class="mt-5 rounded-lg border border-accent-200 p-3 dark:border-dark-700">
      <summary class="cursor-pointer font-semibold">通知与同步记录</summary>
      <article
        v-for="event in store.page.notifications"
        :key="event.id"
        class="mt-3 border-t border-accent-200 pt-3 text-sm dark:border-dark-700"
      >
        <div class="flex flex-wrap justify-between gap-2">
          <span>{{ orderPlatformNames[event.platform] }} ·
            {{ event.topic === 'sync' ? '平台对账' : event.topic }} ·
            {{ processingNames[event.status] }}</span><span>{{ date(event.received_at) }}</span>
        </div>
        <p v-if="event.error" class="mt-1 text-amber-700">{{ event.error }}</p>
        <div
          v-if="event.status === 'failed' || event.status === 'retry'"
          class="mt-2 flex items-center gap-3"
        >
          <span>已尝试 {{ event.attempts }} 次<span v-if="event.status === 'retry'">，下次重试 {{ date(new Date(event.next_attempt * 1000).toISOString()) }}</span></span><button
            class="btn btn-outline"
            :disabled="store.busy"
            @click="store.command('retry', { event_id: event.id })"
          >
            重新处理
          </button>
        </div>
      </article>
      <p v-if="!store.page.notifications.length" class="muted mt-3">尚无通知或同步记录。</p>
    </details>
  </section>
</template>
