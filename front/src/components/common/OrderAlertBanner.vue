<script setup lang="ts">
import { onMounted, onBeforeUnmount } from 'vue'
import { useOrderNotificationsStore } from '@/stores/orderNotifications'
const store = useOrderNotificationsStore()
onMounted(store.start)
onBeforeUnmount(store.stop)
</script>
<template>
  <aside
    v-if="store.page.unread"
    class="fixed bottom-5 left-5 z-40 max-w-sm rounded-lg border border-cyan-300 bg-white p-4 shadow-lg dark:bg-dark-900"
    aria-live="polite"
    aria-label="新订单提醒"
  >
    <p class="font-semibold">有 {{ store.page.unread }} 条待发货提醒</p>
    <p class="mt-1 text-sm">{{ store.page.alerts[0]?.title }}</p>
    <p v-if="store.error" role="alert" class="mt-1 text-sm text-red-600">{{ store.error }}</p>
    <div class="mt-3 flex gap-2">
      <RouterLink class="btn btn-outline" to="/">查看工作台</RouterLink><button
        class="btn btn-outline"
        :disabled="store.busy"
        @click="store.command('acknowledge', { through_id: store.page.latest_alert_id })"
      >
        标记已读
      </button>
    </div>
  </aside>
</template>
