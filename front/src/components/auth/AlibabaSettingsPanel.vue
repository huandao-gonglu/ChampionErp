<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { fetchAlibabaAuthorization, startAlibabaAuthorization } from '@/api/alibabaAuthorization'
import { hasLocalOperations } from '@/utils/desktop'
import type { BackendAlibabaAuthorizationStatus } from '@/types/workflow.generated'

const props = defineProps<{ crossborder?: boolean }>()
const { t } = useI18n()
const status = ref<BackendAlibabaAuthorizationStatus | null>(null)
const opening = ref(false)
const waiting = ref(false)
const error = ref('')
const browserError = ref('')
let timer: number | undefined
let revision = 0
let reading = false
let disposed = false

async function refresh() {
  if (disposed || opening.value || reading) return
  window.clearTimeout(timer)
  const current = revision
  let retryDelay = 2500
  reading = true
  try {
    const next = await fetchAlibabaAuthorization(props.crossborder)
    if (current !== revision) return
    status.value = next
    waiting.value = next.attempt === 'waiting' && !browserError.value
    error.value = ''
  } catch (exc) {
    if (current !== revision) return
    error.value = exc instanceof Error ? exc.message : t('alibabaAuth.failed')
    retryDelay = 10000
  } finally {
    if (current === revision) {
      reading = false
      if (waiting.value || error.value) timer = window.setTimeout(refresh, retryDelay)
    }
  }
}

async function connect() {
  if (opening.value) return
  browserError.value = ''
  // 网页必须在点击事件内打开标签页，否则异步取得授权地址后可能被浏览器拦截。
  const desktop = hasLocalOperations()
  const tab = desktop ? null : window.open('about:blank', '_blank')
  if (!desktop && !tab) {
    browserError.value = t('alibabaAuth.popupBlocked')
    return
  }
  if (tab) tab.opener = null
  window.clearTimeout(timer)
  revision++
  reading = false
  opening.value = true
  error.value = ''
  try {
    const { url } = await startAlibabaAuthorization(props.crossborder)
    try {
      if (tab) tab.location.replace(url)
      else if (window.championDesktop?.openAlibabaAuthorization) await window.championDesktop.openAlibabaAuthorization(url)
      else throw new Error()
    } catch {
      tab?.close()
      waiting.value = false
      browserError.value = t('alibabaAuth.browserFailed')
      return
    }
    waiting.value = true
  } catch (exc) {
    tab?.close()
    error.value = exc instanceof Error ? exc.message : t('alibabaAuth.failed')
  } finally {
    opening.value = false
    if (waiting.value && !disposed) timer = window.setTimeout(refresh, 2500)
  }
}

onMounted(() => {
  void refresh()
  window.addEventListener('focus', refresh)
})
onUnmounted(() => {
  disposed = true
  revision++
  window.clearTimeout(timer)
  window.removeEventListener('focus', refresh)
})
</script>
<template>
  <section class="space-y-4 rounded-lg border border-accent-200 bg-accent-50 p-4 dark:border-dark-700 dark:bg-dark-950/70" :aria-label="t(crossborder ? 'alibabaAuth.crossborderTitle' : 'alibabaAuth.title')">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <h3 class="font-semibold text-accent-950 dark:text-white">{{ t(crossborder ? 'alibabaAuth.crossborderTitle' : 'alibabaAuth.title') }}</h3>
      <span v-if="status" aria-live="polite">{{ t(status.authorized && !status.needs_reauthorization ? 'alibabaAuth.connected' : 'alibabaAuth.disconnected') }}</span>
    </div>
    <p class="text-sm text-accent-500">{{ t(crossborder ? 'alibabaAuth.crossborderIntro' : 'alibabaAuth.intro') }}</p>
    <p v-if="status && !status.application_configured" role="status">{{ t('alibabaAuth.notConfigured') }}</p>
    <p v-else-if="status && !status.can_authorize" role="status">{{ t('alibabaAuth.noCallback') }}</p>
    <p v-if="status?.needs_reauthorization" role="status">{{ t('alibabaAuth.needsAuth') }}</p>
    <p v-if="status?.account_name">{{ t('alibabaAuth.account') }}：{{ status.account_name }}</p>
    <p v-else-if="status?.authorized" class="text-sm">{{ t('alibabaAuth.legacy') }}</p>
    <p v-if="status?.expires_at" class="text-sm">{{ t('alibabaAuth.expiry') }}：{{ new Date(status.expires_at * 1000).toLocaleString() }} <span v-if="status.refreshable">{{ t('alibabaAuth.autoRefresh') }}</span></p>
    <button :data-testid="crossborder ? 'connect-1688-crossborder' : 'connect-1688'" class="btn btn-primary" :disabled="opening || !status?.can_authorize" @click="connect">
      {{ t(status?.authorized || waiting ? 'alibabaAuth.reconnect' : 'alibabaAuth.connect') }}
    </button>
    <p v-if="waiting" role="status">{{ t('alibabaAuth.waiting') }}</p>
    <p v-else-if="status?.attempt === 'failed' || status?.attempt === 'expired'" role="status">{{ t('alibabaAuth.incomplete') }}</p>
    <p v-if="browserError || error" role="alert" class="text-red-600">{{ browserError || error }}</p>
  </section>
</template>
