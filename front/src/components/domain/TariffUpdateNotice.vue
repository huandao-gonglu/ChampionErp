<script setup lang="ts">
import { onActivated, onBeforeUnmount, onMounted, ref } from 'vue'
import { fetchTariffSettings } from '@/api/managedSettings'

const platforms = ref<string[]>([])
const emit = defineEmits<{ versions: [versions: Record<string, string>] }>()
let disposed = false
let loading = false
async function load() {
  if (disposed || loading) return
  loading = true
  try {
    const items = await fetchTariffSettings()
    if (!disposed) {
      platforms.value = items.filter(item => item.update_available).map(item => item.platform === 'ozon' ? 'Ozon' : 'Yandex')
      emit('versions', Object.fromEntries(items.map(item => [item.platform, item.version])))
    }
  } catch { /* 版本详情在运费表设置中提供重试，核价继续使用当前有效表。 */ }
  finally { loading = false }
}
onActivated(() => { void load() })
onMounted(() => {
  void load()
  window.addEventListener('focus', load)
})
onBeforeUnmount(() => {
  disposed = true
  window.removeEventListener('focus', load)
})
</script>

<template>
  <p v-if="platforms.length" role="status" class="pricing-inline-warning mb-4">
    {{ platforms.join('、') }} 运费表有管理员更新，当前继续使用你的个人版本。
    <RouterLink to="/settings?settings_section=tariffs" class="underline">查看并确认升级</RouterLink>
  </p>
</template>
