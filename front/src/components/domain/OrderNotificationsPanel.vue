<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useOrderNotificationsStore } from '@/stores/orderNotifications'
import { orderPlatformNames, orderStateNames } from '@/types/orders'
import OrderAmountDetails from './OrderAmountDetails.vue'
const store = useOrderNotificationsStore()
const publicUrl = ref('')
const copied = ref('')
const processingNames = {
  queued: '等待处理',
  running: '处理中',
  retry: '等待重试',
  failed: '需要处理',
  done: '已完成',
}
function date(value: string) {
  return value ? new Date(value).toLocaleString('zh-CN') : '尚未接收'
}
async function copy(value: string) {
  try {
    await navigator.clipboard.writeText(value)
    copied.value = '回调地址已复制'
  } catch {
    copied.value = '复制失败，请选中地址手动复制'
  }
}
onMounted(async () => {
  await store.loadIntegrations()
  publicUrl.value = store.integrations.public_url
})
</script>
<template>
  <section
    class="rounded-lg border border-accent-200 bg-white p-5 dark:border-dark-700 dark:bg-dark-900"
    aria-label="订单通知中心"
  >
    <div class="flex flex-wrap items-center justify-between gap-3">
      <div>
        <h2 class="card-title">订单通知</h2>
        <p class="muted mt-1">
          三平台订单每 5 分钟自动对账；页面每 5 秒更新。待发货按订单最新状态统计。
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
      <span class="badge-info">{{ store.pendingCount }} 个待发货</span>
      <span class="badge-muted">{{ store.page.unread }} 条未读提醒</span>
      <button
        class="btn btn-outline"
        :disabled="store.busy || !store.page.unread"
        @click="store.command('acknowledge', { through_id: store.page.latest_alert_id })"
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
              <OrderAmountDetails :value="order" :platform="order.platform" />
              <details v-if="order.items.length" class="mt-2">
                <summary class="cursor-pointer text-sm">
                  共 {{ order.items.reduce((total, item) => total + item.quantity, 0) }} 件 · 查看商品明细
                </summary>
                <ul class="mt-2 space-y-3">
                  <li v-for="(item, index) in order.items" :key="`${item.sku}:${index}`" class="border-l-2 border-accent-200 pl-3 dark:border-dark-700">
                    <p>{{ item.title || '暂无商品标题' }}</p>
                    <p class="muted">SKU：{{ item.sku || '未提供' }} · 数量 {{ item.quantity }}</p>
                    <OrderAmountDetails :value="item" :platform="order.platform" />
                    <p class="muted">该 SKU 全部数量的小计</p>
                  </li>
                </ul>
              </details>
            </td>
            <td class="p-2">
              <span :class="order.state === 'pending_shipment' ? 'badge-info' : 'badge-muted'">{{
                orderStateNames[order.state]
              }}</span>
              <p class="muted mt-1">{{ order.status }} {{ order.shipping_status }}</p>
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
    <details class="mt-4 rounded-lg border border-accent-200 p-3 dark:border-dark-700">
      <summary class="cursor-pointer font-semibold">平台回调接入</summary>
      <p class="muted mt-3">
        填写转发到本机服务的公网 HTTPS
        地址，再将各平台回调地址配置到卖家后台。仅公开下面三个回调路径；代理需将 Host
        改为本机地址。回调地址含接入凭据，请勿公开分享。
      </p>
      <form
        class="mt-3 flex flex-wrap gap-2"
        @submit.prevent="store.command('configure', { public_url: publicUrl })"
      >
        <label class="flex-1">公网地址<input
          v-model="publicUrl"
          class="input mt-1 w-full"
          placeholder="https://erp.example.com"
          type="url"
        /></label><button class="btn btn-primary self-end" :disabled="store.busy">保存接入地址</button>
      </form>
      <article
        v-for="integration in store.integrations.platforms"
        :key="integration.platform"
        class="mt-4 text-sm"
      >
        <p class="font-semibold">
          {{ orderPlatformNames[integration.platform] }} ·
          {{ integration.configured ? '账号已配置' : '请先配置店铺授权' }}
        </p>
        <p v-if="integration.callback_url" class="my-2 break-all font-mono text-xs">
          {{ integration.callback_url }}
        </p>
        <button
          v-if="integration.callback_url"
          class="btn btn-outline"
          @click="copy(integration.callback_url)"
        >
          复制回调地址
        </button>
        <p class="muted mt-2">
          最近回调：{{ integration.last_received ? date(integration.last_received) : '尚未接收' }}
        </p>
      </article>
      <p role="status" class="muted mt-2">{{ copied }}</p>
    </details>
  </section>
</template>
