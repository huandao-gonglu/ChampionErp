<script setup lang="ts">
import OrderAlertBanner from '@/components/common/OrderAlertBanner.vue'
import { RouterView } from 'vue-router'
import AiWorkFloatingButton from '@/components/common/AiWorkFloatingButton.vue'
import NavigationProgress from '@/components/common/NavigationProgress.vue'
import ExternalRequestNotice from '@/components/common/ExternalRequestNotice.vue'
import Toast from '@/components/common/Toast.vue'
import DesktopHandoffNotice from '@/components/common/DesktopHandoffNotice.vue'
import { useAccountStore } from '@/stores/account'
import { onMounted, onUnmounted, watch } from 'vue'
import { refreshOperationAvailability, resetOperationAvailability } from '@/api/operationAvailability'
import { OPERATION_AVAILABILITY_CHANGED } from '@/utils/desktop'
const account = useAccountStore()
function refresh() {
  if (account.user) void refreshOperationAvailability()
}
watch(() => account.user?.id, () => { resetOperationAvailability(); refresh() }, { immediate: true })
let unsubscribe: (() => void) | undefined
function refreshAfterChange() {
  resetOperationAvailability()
  refresh()
}
onMounted(() => {
  window.addEventListener('focus', refresh)
  window.addEventListener(OPERATION_AVAILABILITY_CHANGED, refreshAfterChange)
  unsubscribe = window.championDesktop?.onConnectionChanged?.(refreshAfterChange)
})
onUnmounted(() => {
  window.removeEventListener('focus', refresh)
  window.removeEventListener(OPERATION_AVAILABILITY_CHANGED, refreshAfterChange)
  unsubscribe?.()
})
</script>

<template>
  <NavigationProgress />
  <RouterView v-slot="{ Component, route }">
    <KeepAlive>
      <component
        :is="Component"
        v-if="route.meta.keepAlive"
        :key="String(route.name)"
      />
    </KeepAlive>
    <component
      :is="Component"
      v-if="!route.meta.keepAlive"
      :key="String(route.name)"
    />
  </RouterView>
  <AiWorkFloatingButton v-if="account.user" />
  <Toast />
  <DesktopHandoffNotice />
  <ExternalRequestNotice v-if="account.user" />
  <OrderAlertBanner v-if="account.user" />
</template>
