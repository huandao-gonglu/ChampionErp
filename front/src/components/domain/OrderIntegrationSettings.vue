<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useOrderNotificationsStore } from '@/stores/orderNotifications'
import { orderPlatformNames } from '@/types/orders'
const store = useOrderNotificationsStore()
const publicUrl = ref('')
const copied = ref('')
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
    aria-label="订单通知接入设置"
  >
    <h2 class="card-title">订单通知接入</h2>
    <p v-if="store.error" role="alert">{{ store.error }}</p>
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
