import { watch } from 'vue'
import { useRoute } from 'vue-router'
import { useOrderNotificationsStore } from '@/stores/orderNotifications'

export function useOrderCenterEntrySync() {
  const route = useRoute()
  const orders = useOrderNotificationsStore()
  // 只响应导航进入；初次挂载、恢复焦点和详情参数变化不会触发。
  watch(() => route.path === '/' && route.query.tab === 'orders', entered => {
    if (entered) void orders.command('sync', { platform: orders.platform, automatic: true })
  })
}
