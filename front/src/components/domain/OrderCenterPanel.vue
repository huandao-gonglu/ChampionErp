<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useOrderNotificationsStore } from '@/stores/orderNotifications'
import { orderPlatformNames, orderStateNames } from '@/types/orders'
import OrderDetailPanel from './OrderDetailPanel.vue'
import OrderListTable from './OrderListTable.vue'
import OrderNotificationsDrawer from './OrderNotificationsDrawer.vue'
import './orderCenter.css'
const store = useOrderNotificationsStore()
const route = useRoute()
const router = useRouter()
const notificationsOpen = ref(false)
const more = ref<HTMLDetailsElement | null>(null)
const searchText = ref(store.query)
const now = ref(Date.now())
let clock: ReturnType<typeof setInterval> | undefined
const tabs = computed(() => [
  {
    key: '',
    label: '全部',
    count: Object.values(store.page.counts).reduce((a, b) => a + (b || 0), 0),
  },
  ...Object.entries(orderStateNames).map(([key, label]) => ({
    key,
    label: key === 'unknown' ? '待确认' : label,
    count: store.page.counts[key as keyof typeof orderStateNames] || 0,
  })),
])
const syncing = computed(() =>
  store.page.notifications.some(
    (event) =>
      event.topic === 'sync' &&
      ['queued', 'running'].includes(event.status) &&
      (!store.platform || event.platform === store.platform)
  )
)
function search() {
  store.query = searchText.value.trim()
  void store.filter(store.platform, store.state)
}
function closeMore() {
  if (more.value) more.value.open = false
}
function refreshList() {
  closeMore()
  void store.refresh()
}
function enableDesktop() {
  closeMore()
  void store.enableDesktop()
}
function openNotifications() {
  closeMore()
  notificationsOpen.value = true
}
function closeDetail() {
  const query = { ...route.query }
  delete query.order
  void router.push({ query })
}
onMounted(() => {
  store.listActive = true
  void store.refresh()
  clock = setInterval(() => {
    now.value = Date.now()
  }, 60_000)
})
onBeforeUnmount(() => {
  store.listActive = false
  clearInterval(clock)
})
</script>
<template>
  <section class="order-ui order-center" aria-label="订单中心">
    <header class="order-row order-center-header">
      <h2>订单中心</h2>
      <div class="order-actions">
        <span v-if="store.lastCheckedAt" class="order-muted">最近刷新
          {{
            new Date(store.lastCheckedAt).toLocaleTimeString('zh-CN', {
              hour: '2-digit',
              minute: '2-digit',
              hour12: false,
            })
          }}</span>
        <button class="order-button" @click="openNotifications">
          通知 {{ store.summary.unread || 0 }}
        </button>
        <button
          class="order-button order-primary"
          :disabled="store.busy || syncing"
          @click="store.command('sync', { platform: store.platform })"
        >
          {{ syncing ? '同步中…' : store.busy ? '提交中…' : '同步订单' }}
        </button>
        <details ref="more" class="order-more" @keydown.esc="closeMore">
          <summary class="order-button">更多</summary>
          <div class="order-more-menu">
            <button
              @click="refreshList"
            >
              刷新列表
            </button>
            <button
              :disabled="store.desktopEnabled"
              @click="enableDesktop"
            >
              {{ store.desktopEnabled ? '桌面提醒已开启' : '开启桌面提醒' }}
            </button>
            <button @click="openNotifications">通知与同步记录</button>
            <RouterLink :to="{ path: '/', query: { tab: 'auth' } }">平台授权与设置</RouterLink>
          </div>
        </details>
      </div>
    </header>
    <p v-if="store.error" role="alert" class="order-error">{{ store.error }}</p>
    <nav class="order-tabs" aria-label="订单状态">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        :aria-current="store.state === tab.key ? 'page' : undefined"
        :class="{ active: store.state === tab.key }"
        @click="store.filter(store.platform, tab.key)"
      >
        {{ tab.label }} <span>{{ tab.count }}</span>
      </button>
    </nav>
    <form class="order-filters" @submit.prevent="search">
      <input
        v-model="searchText"
        class="order-input"
        type="search"
        aria-label="搜索订单号、SKU 或商品"
        placeholder="搜索订单号、SKU 或商品"
      />
      <button class="order-button">搜索</button>
      <select
        class="order-input order-platform-filter"
        aria-label="平台筛选"
        :value="store.platform"
        @change="store.filter(($event.target as HTMLSelectElement).value, store.state)"
      >
        <option value="">全部平台</option>
        <option v-for="(label, key) in orderPlatformNames" :key="key" :value="key">
          {{ label }}
        </option>
      </select>
      <span class="order-muted order-filter-count">{{ store.page.total }} 个订单</span>
    </form>
    <OrderListTable
      :page="store.page"
      :offset="store.offset"
      :now="now"
      :error="store.error"
      @page="store.filter(store.platform, store.state, $event)"
    />
    <OrderDetailPanel
      v-if="route.query.order"
      :key="String(route.query.order)"
      :order-id="String(route.query.order)"
      @back="closeDetail"
      @updated="store.refresh"
    />
    <OrderNotificationsDrawer :open="notificationsOpen" @close="notificationsOpen = false" />
  </section>
</template>
<style scoped>
.order-center {
  background: var(--order-bg);
}
.order-center-header {
  min-height: 38px;
  margin-bottom: 16px;
}
h2 {
  font-size: 23px;
  font-weight: 700;
}
.order-tabs {
  display: flex;
  gap: 4px;
  margin-bottom: 14px;
  overflow-x: auto;
}
.order-tabs button {
  padding: 10px 20px;
  white-space: nowrap;
  border-radius: 6px 6px 0 0;
  border-bottom: 2px solid transparent;
  background: var(--order-surface);
}
.order-tabs button.active {
  background: #f0fdf4;
  color: #15803d;
  border-bottom-color: #16a34a;
}
.order-filters {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 11px 12px;
  margin-bottom: 12px;
  border-radius: 7px;
  background: var(--order-surface);
}
.order-filters > input {
  max-width: 394px;
}
.order-filters .order-input,
.order-filters .order-button {
  height: 32px;
  min-height: 32px;
}
.order-platform-filter {
  width: 150px;
}
.order-filter-count {
  margin-left: auto;
  white-space: nowrap;
}
.order-more {
  position: relative;
}
.order-more summary {
  list-style: none;
}
.order-more summary::-webkit-details-marker {
  display: none;
}
.order-more-menu {
  position: absolute;
  right: 0;
  top: 40px;
  z-index: 10;
  width: 184px;
  border: 1px solid var(--order-border);
  border-radius: 7px;
  padding: 6px;
  background: var(--order-surface);
  box-shadow: 0 6px 20px #0001;
}
.order-more-menu > * {
  display: block;
  width: 100%;
  text-align: left;
  padding: 9px 12px;
}
.order-more-menu > *:hover {
  background: var(--order-soft);
}
@media (max-width: 700px) {
  .order-filters {
    flex-wrap: wrap;
  }
  .order-filters > input {
    flex: 1;
  }
  .order-filter-count {
    display: none;
  }
  .order-center-header {
    align-items: flex-start;
  }
}
</style>
