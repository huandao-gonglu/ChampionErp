<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { fetchProductResearchSettings, saveProductResearchSettings, testProductResearchSearchProvider } from '@/api/workflow/research'
import type { ProductResearchConfig } from '@/types/workflow'

defineProps<{ embedded?: boolean }>()
const settings = ref<ProductResearchConfig | null>(null)
const busy = ref(false)
const message = ref('')
const error = ref('')
const provider = computed(() => settings.value?.searchProviders.find(item => item.providerStrategy === 'sorftime'))
const supportedMarkets = computed(() => settings.value?.targetMarkets.filter(item => item.platform === 'amazon' && item.site === 'amazon.com') || [])
async function save() {
  if (!settings.value) return
  busy.value = true
  error.value = ''
  message.value = ''
  try {
    settings.value = await saveProductResearchSettings(settings.value)
    message.value = '选品设置已保存'
  } catch (exc) { error.value = exc instanceof Error ? exc.message : '保存失败' }
  finally { busy.value = false }
}
async function test() {
  if (!provider.value) return
  busy.value = true
  error.value = ''
  message.value = ''
  try {
    const result = await testProductResearchSearchProvider(provider.value, {})
    if (!result.ok) throw new Error(result.error || '验证失败')
    const receipts = result.sample.quota_receipts as { request_left?: number; request_consumed?: number }[] | undefined
    message.value = `连接成功，剩余额度 ${receipts?.[0]?.request_left ?? '未知'}，本次消耗 ${receipts?.[0]?.request_consumed ?? '未知'}`
  } catch (exc) { error.value = exc instanceof Error ? exc.message : '验证失败' }
  finally { busy.value = false }
}
function bindMarket(marketId: string, enabled: boolean) {
  const market = settings.value?.targetMarkets.find(item => item.id === marketId)
  if (!market || !provider.value) return
  const binding = market.searchMethods.find(item => item.methodId === provider.value?.id)
  if (binding) binding.enabled = enabled
  else market.searchMethods.push({ methodId: provider.value.id, enabled, prompt: '', configJson: {}, raw: {} })
}
onMounted(async () => {
  try { settings.value = await fetchProductResearchSettings() }
  catch (exc) { error.value = exc instanceof Error ? exc.message : '读取设置失败' }
})
</script>

<template>
  <section class="space-y-5" :class="embedded ? '' : 'card p-6'">
    <div>
      <h2 class="card-title">选品数据源 · Sorftime</h2>
      <p class="muted mt-2">查询 Amazon US 商品，在 1688 查找货源，核对后采集入库。</p>
    </div>
    <template v-if="provider">
      <label class="flex items-center gap-2 text-sm"><input v-model="provider.enabled" type="checkbox" :disabled="busy" />启用 Sorftime</label>
      <label class="block max-w-xl">
        <span class="mb-2 block text-sm font-semibold">开放 API Account-SK</span>
        <input v-model="provider.configJson.api_key" type="password" autocomplete="new-password" class="input" :disabled="busy" />
      </label>
      <div class="space-y-2">
        <p class="text-sm font-semibold">关联市场</p>
        <label v-for="market in supportedMarkets" :key="market.id" class="flex items-center gap-2 text-sm">
          <input type="checkbox" :disabled="busy" :checked="market.searchMethods.some(item => item.methodId === provider?.id && item.enabled)" @change="bindMarket(market.id, ($event.target as HTMLInputElement).checked)" />
          {{ market.displayName }} · {{ market.site }}
        </label>
        <p class="muted">其他已保存市场继续保留，当前接口适配支持 Amazon US。</p>
      </div>
      <p class="muted">每次商品查询或找货预计消耗 2 个额度；确认采集详情与 SKU 预计消耗 2 个额度。连接验证最多按 1 个额度预留，以接口回执为准。</p>
      <div class="flex gap-3">
        <button class="btn btn-primary" :disabled="busy" @click="save">保存设置</button>
        <button class="btn btn-secondary" :disabled="busy" @click="test">验证连接与额度</button>
      </div>
    </template>
    <p v-if="message" role="status" class="text-sm text-success-700">{{ message }}</p>
    <p v-if="error" role="alert" class="text-sm text-danger-700">{{ error }}</p>
  </section>
</template>
