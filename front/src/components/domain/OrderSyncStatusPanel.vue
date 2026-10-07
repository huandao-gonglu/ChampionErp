<script setup lang="ts">
import { computed } from 'vue'
import { useOrderNotificationsStore } from '@/stores/orderNotifications'
import { orderPlatformNames } from '@/types/orders'
import { dateTime } from './orderPresentation'
const store = useOrderNotificationsStore()
const rows = computed(() => (store.page.sync_status || []).filter(
  (row) => !store.platform || row.platform === store.platform
))
const labels = {
  idle: '尚未同步', queued: '等待同步', running: '正在同步', retry: '同步失败，等待重试',
  failed: '同步失败', done: '同步完成', blocked: '同步已暂停，需要处理', cooldown: '同步暂停，等待恢复检查',
}
const needsAttention = (status: string) => ['failed', 'retry', 'blocked', 'cooldown'].includes(status)
</script>
<template>
  <div class="order-sync-status" aria-label="平台同步状态" aria-live="polite">
    <article v-for="row in rows" :key="row.platform" class="order-section">
      <div class="order-row">
        <strong>{{ orderPlatformNames[row.platform] }} · {{ labels[row.status] }}</strong>
        <RouterLink v-if="needsAttention(row.status)" class="order-button" :to="{ path: '/', query: { tab: 'auth', auth_section: 'interruptions' } }">
          前往平台授权处理
        </RouterLink>
      </div>
      <p v-if="needsAttention(row.status)" role="alert" class="order-error">{{ row.error }} 订单可能尚未更新。</p>
      <p class="order-muted">
        最后成功同步：{{ row.last_success_at ? dateTime(row.last_success_at) : '尚无成功记录' }}
        <span v-if="row.next_attempt && !['queued', 'running', 'blocked'].includes(row.status)">
          · {{ row.status === 'done' ? '下次同步' : '下次自动重试' }}：{{ dateTime(new Date(row.next_attempt * 1000).toISOString()) }}
        </span>
      </p>
    </article>
  </div>
</template>
<style scoped>
.order-sync-status { margin-bottom: 14px; }
.order-section { padding: 10px 12px; }
</style>
