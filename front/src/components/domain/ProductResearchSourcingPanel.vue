<script setup lang="ts">
import { computed, ref } from 'vue'
import { searchProductResearchSuppliers, importProductResearchSupplier } from '@/api/workflow/research'
import type { HotProductCandidate, BackendProductResearchSourcing, BackendSorftimeQuotaReceipt } from '@/types/workflow'

const props = defineProps<{ candidate: HotProductCandidate; runId: string }>()
const emit = defineEmits<{ imported: [] }>()
const sourcing = ref<BackendProductResearchSourcing>((props.candidate.raw.sourcing || {}) as BackendProductResearchSourcing)
const keyword = ref(sourcing.value.query?.keyword || '')
const mode = ref(sourcing.value.query?.mode || 'keyword')
const busy = ref(false)
const error = ref('')
const selectedId = ref('')
const confirmed = ref(false)
const importedId = ref(props.candidate.importedProductId || '')
const selected = computed(() => sourcing.value.items?.find(item => item.product_id === selectedId.value))
const receipts = ref<BackendSorftimeQuotaReceipt[]>(sourcing.value.quota_receipts || [])
async function search() {
  busy.value = true; error.value = ''; confirmed.value = false; selectedId.value = ''
  try {
    const result = await searchProductResearchSuppliers({ run_id: props.runId, candidate_id: props.candidate.id, mode: mode.value, keyword: keyword.value })
    sourcing.value = result.sourcing as BackendProductResearchSourcing
    receipts.value = result.cached ? [] : sourcing.value.quota_receipts || []
  } catch (exc) { sourcing.value = {}; error.value = exc instanceof Error ? exc.message : '查找货源失败' }
  finally { busy.value = false }
}
async function importProduct() {
  if (!selected.value || !confirmed.value) return
  busy.value = true; error.value = ''
  try {
    const result = await importProductResearchSupplier({ run_id: props.runId, candidate_id: props.candidate.id, supplier_id: selected.value.product_id, confirmed: true })
    importedId.value = String(result.product_id || '')
    receipts.value = result.quota_receipts as BackendSorftimeQuotaReceipt[] || []
    emit('imported')
  } catch (exc) { error.value = exc instanceof Error ? exc.message : '采集入库失败' }
  finally { busy.value = false }
}
</script>

<template>
  <section class="rounded-lg border border-accent-200 bg-white p-5 shadow-card dark:border-dark-700 dark:bg-dark-900/80">
    <h2 class="card-title">1688 找货与入库</h2>
    <p class="muted mt-2">搜索结果可能不相关，请核对款式和规格。仅凭图片相似不能确认同款。</p>
    <label class="mt-4 block text-sm">找货方式
      <select v-model="mode" class="input mt-2" :disabled="busy"><option value="keyword">中文关键词</option><option value="image">候选主图</option></select>
    </label>
    <label v-if="mode === 'keyword'" class="mt-3 block text-sm">货源关键词<input v-model="keyword" class="input mt-2" placeholder="例如：水槽下收纳架" maxlength="100" :disabled="busy" /></label>
    <button class="btn btn-secondary mt-3 w-full" :disabled="busy || (mode === 'keyword' && !keyword.trim())" @click="search">{{ busy ? '处理中' : '查找货源（约 2 额度）' }}</button>
    <p class="muted mt-2">相同查询复用已保存结果，不重复扣费。</p>
    <div class="mt-4 max-h-96 space-y-3 overflow-auto">
      <label v-for="supplier in sourcing.items || []" :key="supplier.product_id" class="block rounded-lg border border-accent-200 p-3 dark:border-dark-700">
        <div class="flex gap-2"><input v-model="selectedId" type="radio" :value="supplier.product_id" :disabled="busy" @change="confirmed = false" /><span class="text-sm font-semibold">{{ supplier.title }}</span></div>
        <img v-if="supplier.image_url" :src="supplier.image_url" :alt="supplier.title" loading="lazy" class="mt-2 h-20 w-20 rounded object-contain" />
        <p class="mt-2 text-sm">¥ {{ supplier.price_cny ?? '暂无' }} · 起订 {{ supplier.min_order_quantity ?? '暂无' }} 件</p>
        <p class="muted">{{ supplier.store_name || '店铺未提供' }} · 服务分 {{ supplier.service_score ?? '暂无' }}</p>
        <p class="muted">近 30 天销量 {{ supplier.monthly_sales ?? '暂无' }} · 复购率 {{ supplier.repurchase_rate ?? '暂无' }}%</p>
        <a class="mt-2 inline-block text-sm underline" :href="supplier.source_url" target="_blank" rel="noreferrer">核对货源详情</a>
      </label>
      <p v-if="sourcing.items && !sourcing.items.length" class="muted">暂无货源，请调整关键词后再次查询。</p>
    </div>
    <template v-if="selected">
      <label class="mt-4 flex gap-2 text-sm"><input v-model="confirmed" type="checkbox" :disabled="busy" />我已核对所选货源的款式和规格，确认采集到商品库</label>
      <button class="btn btn-primary mt-3 w-full" :disabled="busy || !confirmed" @click="importProduct">确认货源并采集入库（约 2 额度）</button>
      <p class="muted mt-2">采集标题、主图、人民币采购价和 SKU；包装尺寸、重量及材质需后续核对补充。</p>
    </template>
    <p v-if="importedId" role="status" class="mt-3 text-sm text-success-700">已入库：{{ importedId }}。可在商品库继续核价与编辑。</p>
    <p v-for="(receipt, index) in receipts" :key="index" class="muted mt-2">消耗 {{ receipt.request_consumed ?? '未知' }}，剩余 {{ receipt.request_left ?? '未知' }}。</p>
    <p v-if="error" role="alert" class="mt-3 text-sm text-danger-700">{{ error }}</p>
  </section>
</template>
