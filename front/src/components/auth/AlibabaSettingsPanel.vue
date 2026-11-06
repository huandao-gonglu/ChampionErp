<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { fetchAlibabaAuthorization, startAlibabaAuthorization, completeAlibabaAuthorization, testAlibabaCrossborderProduct, testAlibabaOrder } from '@/api/alibabaAuthorization'
import { desktopOperationReason } from '@/utils/desktop'
import { openAuthLink } from '@/api/workflow/settings'
import type { BackendAlibabaAuthorizationStatus } from '@/types/workflow.generated'

const props = defineProps<{ crossborder?: boolean }>()
const { t } = useI18n()
const status = ref<BackendAlibabaAuthorizationStatus | null>(null)
const busy = ref(false)
const error = ref('')
const message = ref('')
const authUrl = ref('')
const callbackUrl = ref('')
const orderNumber = ref('')
const productId = ref('')
async function run(action: () => Promise<void>) {
  if (busy.value) return
  busy.value = true; error.value = ''; message.value = ''
  try { await action() }
  catch (exc) { error.value = exc instanceof Error ? exc.message : t('alibabaAuth.failed') }
  finally { busy.value = false }
}
function load() { return run(async () => { status.value = await fetchAlibabaAuthorization(props.crossborder) }) }
function connect() {
  return run(async () => {
    callbackUrl.value = ''
    authUrl.value = (await startAlibabaAuthorization(props.crossborder)).url
    if (desktopOperationReason()) return
    const result = await openAuthLink(authUrl.value)
    if (!result.ok) throw new Error(result.error || t('alibabaAuth.failed'))
  })
}
function complete() {
  return run(async () => {
    try {
      status.value = await completeAlibabaAuthorization(callbackUrl.value.trim(), props.crossborder)
      authUrl.value = ''; message.value = t('alibabaAuth.success')
    } finally { callbackUrl.value = '' }
  })
}
function testOrder() {
  return run(async () => { await testAlibabaOrder(orderNumber.value.trim()); message.value = t('alibabaAuth.querySuccess') })
}
function testProduct() {
  return run(async () => {
    const result = await testAlibabaCrossborderProduct(productId.value.trim())
    message.value = t('alibabaAuth.productResult', { total: result.sku_count, matched: result.spec_id_count })
  })
}
function onFocus() { if (authUrl.value) void load() }
onMounted(() => { void load(); window.addEventListener('focus', onFocus) })
onUnmounted(() => window.removeEventListener('focus', onFocus))
</script>
<template>
  <section class="space-y-4 rounded-lg border border-accent-200 bg-accent-50 p-4 dark:border-dark-700 dark:bg-dark-950/70" :aria-label="t(crossborder ? 'alibabaAuth.crossborderTitle' : 'alibabaAuth.title')">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <h3 class="font-semibold text-accent-950 dark:text-white">{{ t(crossborder ? 'alibabaAuth.crossborderTitle' : 'alibabaAuth.title') }}</h3>
      <span>{{ t(status?.authorized && !status.needs_reauthorization ? 'alibabaAuth.connected' : 'alibabaAuth.disconnected') }}</span>
    </div>
    <p class="text-sm text-accent-500">{{ t(crossborder ? 'alibabaAuth.crossborderIntro' : 'alibabaAuth.intro') }}</p>
    <p v-if="status && !status.application_configured" role="status">{{ t('alibabaAuth.notConfigured') }}</p>
    <p v-else-if="status && !status.can_authorize" role="status">{{ t('alibabaAuth.noCallback') }}</p>
    <p v-if="status?.needs_reauthorization" role="status">{{ t('alibabaAuth.needsAuth') }}</p>
    <p v-if="status?.member_id">{{ t('alibabaAuth.account') }}：{{ status.member_id }}</p>
    <p v-else-if="status?.authorized" class="text-sm">{{ t('alibabaAuth.legacy') }}</p>
    <p v-if="status?.expires_at" class="text-sm">{{ t('alibabaAuth.expiry') }}：{{ new Date(status.expires_at * 1000).toLocaleString() }} <span v-if="status.refreshable">{{ t('alibabaAuth.autoRefresh') }}</span></p>
    <div class="flex flex-wrap gap-3">
      <button :data-testid="crossborder ? 'connect-1688-crossborder' : 'connect-1688'" class="btn btn-primary" :disabled="busy || !status?.can_authorize" @click="connect">{{ t(status?.authorized ? 'alibabaAuth.reconnect' : 'alibabaAuth.connect') }}</button>
      <button class="btn btn-outline" :disabled="busy" @click="load">{{ t('alibabaAuth.refresh') }}</button>
    </div>
    <div v-if="authUrl" class="space-y-3">
      <a :href="authUrl" target="_blank" rel="noopener noreferrer" class="text-blue-600 underline">{{ t('alibabaAuth.open') }}</a>
      <p class="text-sm">{{ t('alibabaAuth.hint') }}</p>
      <details>
        <summary class="cursor-pointer text-sm">{{ t('alibabaAuth.manual') }}</summary>
        <form class="mt-3 flex flex-wrap gap-3" @submit.prevent="complete">
          <input v-model="callbackUrl" type="password" autocomplete="off" class="input flex-1" :aria-label="t('alibabaAuth.paste')" :placeholder="t('alibabaAuth.paste')" />
          <button class="btn btn-outline" :disabled="busy || !callbackUrl.trim()">{{ t('alibabaAuth.complete') }}</button>
        </form>
      </details>
    </div>
    <form v-if="crossborder" class="flex flex-wrap gap-3" @submit.prevent="testProduct">
      <input v-model="productId" class="input flex-1" inputmode="numeric" :aria-label="t('alibabaAuth.productId')" :placeholder="t('alibabaAuth.productId')" />
      <button class="btn btn-outline" :disabled="busy || !status?.authorized || status.needs_reauthorization || !/^[1-9][0-9]{0,18}$/.test(productId.trim())">{{ t('alibabaAuth.testProduct') }}</button>
    </form>
    <form v-else class="flex flex-wrap gap-3" @submit.prevent="testOrder">
      <input v-model="orderNumber" class="input flex-1" inputmode="numeric" :aria-label="t('alibabaAuth.order')" :placeholder="t('alibabaAuth.order')" />
      <button class="btn btn-outline" :disabled="busy || !status?.authorized || status.needs_reauthorization || !/^[0-9]{1,30}$/.test(orderNumber.trim())">{{ t('alibabaAuth.query') }}</button>
    </form>
    <p v-if="error" role="alert" class="text-red-600">{{ error }}</p>
    <p v-if="message" role="status" class="text-emerald-600">{{ message }}</p>
  </section>
</template>
