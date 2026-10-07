<script setup lang="ts">
import OrderSyncStatusPanel from './OrderSyncStatusPanel.vue'
import WorkspaceDialog from '@/components/shared/WorkspaceDialog.vue'
import { useOrderNotificationsStore } from '@/stores/orderNotifications'
import { orderPlatformNames } from '@/types/orders'
import { dateTime } from './orderPresentation'
defineProps<{ open: boolean }>()
defineEmits<{ close: [] }>()
const store = useOrderNotificationsStore()
const processingNames = {
  queued: '等待处理',
  running: '处理中',
  retry: '等待重试',
  failed: '同步失败',
  done: '已完成',
}
</script>
<template>
  <WorkspaceDialog
    :open="open"
    title="通知与同步"
    variant="drawer"
    width="540px"
    class="order-ui"
    :close-disabled="store.busy"
    @close="$emit('close')"
  >
    <div class="order-row">
      <span class="order-muted">{{ store.summary.unread }} 条未读</span><button
        class="order-button"
        :disabled="store.busy || !store.summary.unread"
        @click="
          store.command('acknowledge', {
            through_id: store.summary.latest_alert_id,
          })
        "
      >
        全部已读
      </button>
    </div>
    <p v-if="store.commandError || store.error" class="order-error" role="alert">{{ store.commandError || store.error }}</p>
    <OrderSyncStatusPanel />
    <article v-for="event in store.page.notifications" :key="event.id" class="order-section">
      <div class="order-row">
        <h4 class="font-semibold">{{ orderPlatformNames[event.platform] }}</h4>
        <span
          class="order-badge"
          :data-tone="
            event.status === 'failed'
              ? 'red'
              : event.status === 'retry'
                ? 'amber'
                : event.status === 'done'
                  ? 'green'
                  : 'neutral'
          "
        >{{ processingNames[event.status] }}</span>
      </div>
      <p class="mt-3">
        {{ event.error || (event.topic === 'sync' ? '平台订单同步' : event.topic) }}
      </p>
      <p class="order-muted mt-3">
        {{ dateTime(event.received_at)
        }}<span v-if="event.status === 'retry'">
          · 下次重试
          {{ dateTime(new Date(event.next_attempt * 1000).toISOString()) }}</span>
      </p>
      <div v-if="event.status === 'failed' || event.status === 'retry'" class="order-actions mt-5">
        <RouterLink class="order-button" :to="{ path: '/', query: { tab: 'auth' } }">
          店铺授权设置
        </RouterLink>
        <button
          class="order-link"
          :disabled="store.busy"
          @click="store.command('retry', { event_id: event.id })"
        >
          {{ event.status === 'retry' ? '立即重试' : '重试' }}
        </button>
        <span class="order-muted">已尝试 {{ event.attempts }} 次</span>
      </div>
    </article>
    <p v-if="!store.page.notifications.length" class="order-muted py-12 text-center">
      尚无通知或同步记录
    </p>
  </WorkspaceDialog>
</template>
