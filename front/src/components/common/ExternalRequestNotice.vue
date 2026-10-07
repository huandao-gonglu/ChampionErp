<script setup lang="ts">
import { computed, watch, onMounted, onBeforeUnmount } from 'vue'
import { apiClient } from '@/api/client'
import { externalRequestNotices, externalObservationRevision, observedExternalOperations, acceptExternalNotices, dismissExternalNotice, requestPlatformName } from '@/utils/externalRequestNotices'
const current = computed(() => externalRequestNotices.value[0])
const platforms = computed(() => [...new Set(current.value?.items.map(item => item.platform) || [])])
const title = computed(() => `${current.value?.source}：${platforms.value.map(requestPlatformName).join('、')} 请求受阻`)
const destination = computed(() => ({ path: '/', query: { tab: 'auth', auth_section: 'interruptions', request_platform: platforms.value } }))
let timer: ReturnType<typeof setTimeout> | undefined
let stopped = false
let checking = false
let checkAgain = false
async function check() {
  clearTimeout(timer)
  if (checking) { checkAgain = true; return }
  checking = true
  const ids = observedExternalOperations()
  try {
    if (ids.length) {
      const params = new URLSearchParams()
      ids.forEach(id => params.append('operation_id', id))
      const { data } = await apiClient.get('/api/external-requests/status', { params, timeout: 10000 })
      if (!stopped) acceptExternalNotices(data.notices || [])
    }
  } catch { /* 原功能和授权页保留错误；提醒读取失败不冒充业务失败。 */ }
  finally {
    checking = false
    if (!stopped) timer = setTimeout(check, checkAgain ? 0 : 5000)
    checkAgain = false
  }
}
watch(externalObservationRevision, check)
onMounted(check)
onBeforeUnmount(() => { stopped = true; clearTimeout(timer) })
</script>
<template>
  <aside
    v-if="current" role="alert" aria-label="平台请求暂停"
    class="fixed right-5 top-20 z-50 max-h-[80vh] max-w-sm overflow-y-auto rounded-lg border border-amber-300 bg-white p-4 shadow-lg dark:border-amber-700 dark:bg-dark-900"
  >
    <p class="font-semibold">{{ title }}</p>
    <ul class="mt-3 space-y-2">
      <li v-for="(item, index) in current.items" :key="index">
        <strong>{{ requestPlatformName(item.platform) }}</strong>：{{ item.message }}
      </li>
    </ul>
    <p v-if="current.independentPlatforms" class="mt-3 text-sm">本次上述平台的请求未完成，其他平台继续独立处理。</p>
    <p v-else class="mt-3 text-sm">本提醒仅说明上述平台的请求受阻，不代表整个操作的最终结果。</p>
    <div class="mt-3 flex gap-3">
      <RouterLink class="btn btn-primary" :to="destination" @click="dismissExternalNotice">
        前往平台授权处理
      </RouterLink>
      <button class="btn btn-outline" @click="dismissExternalNotice">关闭</button>
    </div>
  </aside>
</template>
