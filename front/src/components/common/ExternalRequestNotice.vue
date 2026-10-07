<script setup lang="ts">
import { onMounted, onBeforeUnmount } from 'vue'
import { apiClient } from '@/api/client'
import { externalRequestNotice, observedExternalOperations, acceptExternalNotices } from '@/utils/externalRequestNotices'
let timer: ReturnType<typeof setTimeout> | undefined
let stopped = false
async function check() {
  const ids = observedExternalOperations()
  try {
    if (ids.length) {
      const params = new URLSearchParams()
      ids.forEach(id => params.append('operation_id', id))
      const { data } = await apiClient.get('/api/external-requests/status', { params, timeout: 10000 })
      if (!stopped) acceptExternalNotices(data.notices || [])
    }
  } catch { /* 原功能和授权页保留错误；提醒读取失败不冒充业务失败。 */ }
  if (!stopped) timer = setTimeout(check, 5000)
}
onMounted(check)
onBeforeUnmount(() => { stopped = true; clearTimeout(timer) })
</script>
<template>
  <aside
    v-if="externalRequestNotice" role="alert" aria-label="平台请求暂停"
    class="fixed right-5 top-20 z-50 max-w-sm rounded-lg border border-amber-300 bg-white p-4 shadow-lg dark:border-amber-700 dark:bg-dark-900"
  >
    <p>{{ externalRequestNotice }}</p>
    <div class="mt-3 flex gap-3">
      <RouterLink
        class="btn btn-primary" :to="{ path: '/', query: { tab: 'auth', auth_section: 'interruptions' } }"
        @click="externalRequestNotice = ''"
      >
        前往平台授权
      </RouterLink>
      <button class="btn btn-outline" @click="externalRequestNotice = ''">关闭</button>
    </div>
  </aside>
</template>
