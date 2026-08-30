<script setup lang="ts">
import { computed } from 'vue'
import type { MercadoLibreMarketPublication, MercadoLibreUserProduct, UnknownRecord } from '@/types/workflow'

const props = defineProps<{
  userProducts: MercadoLibreUserProduct[]
  status: string
  page: number
  perPage: number
  total: number
  totalPages: number
  refreshErrors?: UnknownRecord[]
  refreshScope?: string
  checkedAt?: string
  loading: boolean
  error?: string
}>()

const emit = defineEmits<{
  refresh: [status: string, page?: number, perPage?: number, refreshIdentityMapping?: boolean]
  pauseUserProduct: [userProduct: MercadoLibreUserProduct]
}>()

const statusLabels: Record<string, string> = {
  active: '在售',
  paused: '已暂停',
  closed: '已结束',
  pending: '处理中',
  failed: '失败',
  partial: '部分成功',
  all: '全部',
}

const pageCount = computed(() => Math.max(1, props.totalPages || Math.ceil(props.total / Math.max(1, props.perPage)) || 1))
const pageStart = computed(() => props.total ? ((props.page - 1) * props.perPage) + 1 : 0)
const pageEnd = computed(() => Math.min(props.total, (props.page - 1) * props.perPage + props.userProducts.length))

function badgeClass(status: string) {
  const value = String(status || '').toLowerCase()
  if (value === 'active') return 'badge-success'
  if (value === 'paused' || value === 'pending') return 'badge-info'
  if (value === 'failed') return 'badge-danger'
  return 'badge-muted'
}

function statusLabel(status: string) {
  return statusLabels[String(status || '').toLowerCase()] || status || '-'
}

function marketKey(market: MercadoLibreMarketPublication, index: number) {
  return `${market.siteId}:${market.userProductId || market.itemId || index}`
}

function marketError(error: MercadoLibreMarketPublication['error']) {
  if (typeof error === 'string') return error
  if (Array.isArray(error)) return JSON.stringify(error)
  return String(error.message || error.code || JSON.stringify(error))
}

function refreshError(error: UnknownRecord) {
  return String(error.message || error.error || error.code || JSON.stringify(error))
}

function requestPause(userProduct: MercadoLibreUserProduct) {
  const id = userProduct.sitelessUserProductId
  const confirmed = window.confirm(`确认暂停 Siteless User Product ${id}？该操作作用于整个 User Product，而不是单个市场。`)
  if (confirmed) emit('pauseUserProduct', userProduct)
}

function refreshStatus(status: string) {
  emit('refresh', status, 1, props.perPage, false)
}

function refreshPerPage(value: string) {
  const next = Number.parseInt(value, 10)
  emit('refresh', props.status, 1, Number.isFinite(next) ? next : props.perPage, false)
}

function goPage(page: number) {
  emit('refresh', props.status, Math.min(Math.max(1, page), pageCount.value), props.perPage, false)
}
</script>

<template>
  <section class="rounded-lg border border-accent-200 bg-white p-5 shadow-card dark:border-dark-700 dark:bg-dark-900/80">
    <div class="flex flex-wrap items-start justify-between gap-4">
      <div class="min-w-0">
        <p class="text-xs font-semibold uppercase text-primary-600 dark:text-primary-300">ML User Products</p>
        <h2 class="mt-2 card-title">Mercado Libre 已发布商品</h2>
        <p class="muted mt-1">按 Siteless User Product 展示全局商品及各销售市场投影；暂停操作作用于整个 Siteless User Product。</p>
      </div>
      <div class="grid w-full gap-3 sm:grid-cols-3 lg:w-auto">
        <select :value="props.status" class="input sm:w-40" :disabled="props.loading" @change="refreshStatus(($event.target as HTMLSelectElement).value)">
          <option value="active">在售</option>
          <option value="paused">已暂停</option>
          <option value="partial">部分成功</option>
          <option value="closed">已结束</option>
          <option value="all">全部</option>
        </select>
        <select :value="props.perPage" class="input sm:w-32" :disabled="props.loading" @change="refreshPerPage(($event.target as HTMLSelectElement).value)">
          <option :value="25">25 条/页</option>
          <option :value="50">50 条/页</option>
          <option :value="100">100 条/页</option>
        </select>
        <button class="btn btn-outline" :disabled="props.loading" @click="emit('refresh', props.status, props.page, props.perPage, true)">{{ props.loading ? '映射对账中...' : '对账身份映射' }}</button>
      </div>
    </div>

    <div v-if="props.error" class="mt-4 rounded-lg border border-rose-200 bg-rose-50 p-4 text-sm font-medium text-rose-700 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-200">{{ props.error }}</div>
    <div v-if="props.refreshScope" data-testid="ml-user-products-refresh-scope" class="mt-4 rounded-lg border border-sky-200 bg-sky-50 p-4 text-sm text-sky-800 dark:border-sky-500/30 dark:bg-sky-500/10 dark:text-sky-200">
      <p class="font-semibold">刷新范围：{{ props.refreshScope }}</p>
      <p v-if="props.refreshScope === 'identity_mapping_only'" class="mt-1">本次只对账 Siteless、市场 User Product 与 Item 的身份映射；状态和价格仍来自本地 publication 快照，未从 Mercado 远端同步。</p>
      <p v-else class="mt-1">当前内容来自本地 publication 快照。</p>
    </div>
    <div v-if="props.refreshErrors?.length" class="mt-4 rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200">
      <p class="font-semibold">部分身份映射对账失败</p>
      <ul class="mt-1 list-disc space-y-1 pl-5"><li v-for="(item, index) in props.refreshErrors" :key="`${index}:${refreshError(item)}`">{{ refreshError(item) }}</li></ul>
    </div>

    <div class="mt-5 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-accent-200 bg-accent-50 p-3 text-sm text-accent-500 dark:border-dark-700 dark:bg-dark-950/70 dark:text-accent-400">
      <span>当前筛选：{{ statusLabel(props.status) }}，第 {{ props.page }} / {{ pageCount }} 页，显示 {{ pageStart }}-{{ pageEnd }}，共 {{ props.total }} 个 User Products。<template v-if="props.checkedAt"> {{ props.refreshScope === 'identity_mapping_only' ? '映射检查于' : '快照生成于' }} {{ props.checkedAt }}</template></span>
      <div class="flex items-center gap-2">
        <button class="btn btn-outline py-1.5" :disabled="props.loading || props.page <= 1" @click="goPage(props.page - 1)">上一页</button>
        <button class="btn btn-outline py-1.5" :disabled="props.loading || props.page >= pageCount" @click="goPage(props.page + 1)">下一页</button>
      </div>
    </div>

    <div class="mt-5 space-y-4">
      <article v-for="userProduct in props.userProducts" :key="userProduct.sitelessUserProductId" data-testid="ml-user-product" class="overflow-hidden rounded-lg border border-accent-200 dark:border-dark-700">
        <header class="flex flex-wrap items-start justify-between gap-4 bg-accent-50 p-4 dark:bg-dark-950/70">
          <div class="flex min-w-0 gap-3">
            <img v-if="userProduct.thumbnail" :src="userProduct.thumbnail" class="size-12 shrink-0 rounded-lg object-cover" />
            <div v-else class="flex size-12 shrink-0 items-center justify-center rounded-lg bg-accent-100 text-[10px] font-bold text-accent-500 dark:bg-dark-800 dark:text-accent-300">UP</div>
            <div class="min-w-0">
              <div class="truncate font-semibold text-accent-950 dark:text-white" :title="userProduct.title || userProduct.familyName">{{ userProduct.title || userProduct.familyName || '未命名 User Product' }}</div>
              <div class="mt-1 break-all font-mono text-xs text-accent-600 dark:text-accent-300">Siteless ID：{{ userProduct.sitelessUserProductId || '-' }}</div>
              <div class="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs text-accent-500 dark:text-accent-400">
                <span v-if="userProduct.productId">内部商品：{{ userProduct.productId }}</span>
                <span v-if="userProduct.draftId">草稿：{{ userProduct.draftId }}</span>
                <span v-if="userProduct.model">型号：{{ userProduct.model }}</span>
                <span v-if="userProduct.accountUserId">账号：{{ userProduct.accountUserId }}</span>
                <span v-if="userProduct.sitelessFamilyId">Family：{{ userProduct.sitelessFamilyId }}</span>
                <span v-if="userProduct.parentUserProductId">Parent UP：{{ userProduct.parentUserProductId }}</span>
                <span v-if="userProduct.parentItemId">Parent Item：{{ userProduct.parentItemId }}</span>
                <span v-if="userProduct.sellerId">Seller：{{ userProduct.sellerId }}</span>
              </div>
            </div>
          </div>
          <div class="flex flex-wrap items-center gap-2">
            <span :class="badgeClass(userProduct.status)">{{ statusLabel(userProduct.status) }}</span>
            <button class="btn btn-secondary whitespace-nowrap px-3 py-1.5 text-xs" :disabled="props.loading || userProduct.status === 'closed' || userProduct.status === 'paused' || !userProduct.sitelessUserProductId" @click="requestPause(userProduct)">
              {{ userProduct.status === 'closed' || userProduct.status === 'paused' ? '已暂停' : '暂停整个 User Product' }}
            </button>
          </div>
        </header>

        <div class="overflow-x-auto">
          <table class="min-w-[900px] w-full table-fixed text-left text-sm">
            <colgroup><col class="w-[9%]" /><col class="w-[12%]" /><col class="w-[16%]" /><col class="w-[16%]" /><col class="w-[12%]" /><col class="w-[10%]" /><col class="w-[10%]" /><col /></colgroup>
            <thead class="border-y border-accent-200 text-xs text-accent-500 dark:border-dark-700 dark:text-accent-400">
              <tr><th class="p-3">市场</th><th class="p-3">物流操作</th><th class="p-3">Market UP ID</th><th class="p-3">Item ID</th><th class="p-3">刊登类型</th><th class="p-3">状态</th><th class="p-3">价格</th><th class="p-3">更新时间 / 错误</th></tr>
            </thead>
            <tbody class="divide-y divide-accent-100 dark:divide-dark-800">
              <tr v-for="(market, index) in userProduct.markets" :key="marketKey(market, index)" data-testid="ml-market-publication" class="align-top">
                <td class="p-3 font-semibold text-accent-950 dark:text-white">{{ market.siteId || '-' }}</td>
                <td class="p-3 text-accent-700 dark:text-accent-200">{{ market.logisticType || '-' }}</td>
                <td class="break-all p-3 font-mono text-xs text-accent-700 dark:text-accent-200">{{ market.userProductId || '-' }}</td>
                <td class="break-all p-3 font-mono text-xs text-accent-700 dark:text-accent-200">{{ market.itemId || '-' }}</td>
                <td class="p-3 text-xs text-accent-700 dark:text-accent-200">{{ market.listingTypeId || '-' }}</td>
                <td class="p-3"><span :class="badgeClass(market.status)">{{ statusLabel(market.status) }}</span></td>
                <td class="p-3 font-semibold text-accent-950 dark:text-white">{{ market.price ?? '-' }} {{ market.currencyId }}</td>
                <td class="p-3 text-xs"><div class="text-accent-500 dark:text-accent-400">{{ market.updatedAt || '-' }}</div><div v-if="marketError(market.error)" class="mt-1 text-rose-600 dark:text-rose-200">{{ marketError(market.error) }}</div></td>
              </tr>
              <tr v-if="!userProduct.markets.length"><td colspan="8" class="p-5 text-center text-accent-500 dark:text-accent-300">尚未生成市场投影。</td></tr>
            </tbody>
          </table>
        </div>
      </article>
      <div v-if="!props.userProducts.length" class="rounded-lg border border-accent-200 p-8 text-center text-accent-500 dark:border-dark-700 dark:text-accent-300">暂无 User Products，或当前状态筛选下没有结果。</div>
    </div>

    <div class="mt-4 flex flex-wrap items-center justify-end gap-2 text-sm text-accent-500 dark:text-accent-400">
      <button class="btn btn-outline py-1.5" :disabled="props.loading || props.page <= 1" @click="goPage(props.page - 1)">上一页</button>
      <span>第 {{ props.page }} / {{ pageCount }} 页</span>
      <button class="btn btn-outline py-1.5" :disabled="props.loading || props.page >= pageCount" @click="goPage(props.page + 1)">下一页</button>
    </div>
  </section>
</template>
