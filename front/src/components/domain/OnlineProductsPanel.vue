<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import OnlineContentEditor from './OnlineContentEditor.vue'
import OnlineBuyerLinks from './OnlineBuyerLinks.vue'
import { useAiPageContext } from '@/composables/useAiPageContext'
import { useBackdropDismiss } from '@/composables/useBackdropDismiss'
import { fetchOnlineDetail, fetchOnlineProducts, onlineAction, refreshOnlineStatus, type OnlineJob, type OnlineListing, type OnlineOperation, type OnlinePage, type OnlinePlatform } from '@/api/onlineProducts'

const platform = ref<OnlinePlatform>('mercadolibre')
const platforms: Record<OnlinePlatform, string> = {mercadolibre: 'Mercado Libre', ozon: 'Ozon', yandex: 'Yandex Market'}
const labels: Record<string, string> = {active: '在售', paused: '已停售', closed: '已关闭', under_review: '审核中', queued: '排队中', running: '执行中', submitted: '已提交', waiting_confirmation: '等待平台确认', confirmed: '已生效', PUBLISHED: '在售', CHECKING: '审核中', DISABLED: '不可售', ARCHIVED: '已归档', HAS_CARD_CAN_UPDATE: '卡片可更新', partial: '部分成功', failed: '失败', outcome_unknown: '结果未知', sync: '同步店铺商品', price: '调整价格', stock: '修改库存', content: '编辑内容', sale_state: '停售 / 恢复'}
const page = ref<OnlinePage | null>(null)
const expandedGroups = ref(new Set<string>())
const listingGroups = computed(() => {
  const items = new Map(page.value?.items.map(row => [row.id, row]))
  return page.value?.groups.map(group => ({
    ...group,
    rows: group.item_ids.flatMap(id => { const row = items.get(id); return row ? [row] : [] }),
  })) || []
})
function toggleGroup(id: string) {
  if (expandedGroups.value.has(id)) expandedGroups.value.delete(id)
  else expandedGroups.value.add(id)
}
const query = ref(''), status = ref(''), market = ref(''), currentPage = ref(1)
const loading = ref(false), busy = ref(false), error = ref(''), notice = ref('')
const checkingStatus = ref(new Set<string>())
const statusErrors = ref<Record<string, string>>({})
let viewEpoch = 0
const showRecords = ref(false), selectedJob = ref<OnlineJob | null>(null), selected = ref<OnlineListing | null>(null)
const modal = ref<OnlineOperation | 'sync' | ''>('')
const detailBackdrop = useBackdropDismiss(() => { if (!modal.value) selected.value = null })
const modalBackdrop = useBackdropDismiss(() => { if (!busy.value) modal.value = '' })
watch(selected, detailBackdrop.resetBackdropPointer)
watch(modal, modalBackdrop.resetBackdropPointer)
useAiPageContext(() => ({ page: 'onlineProducts', platform: platform.value, listing_id: selected.value?.id }), 10)
const scope = ref(''), value = ref('')
const saleTarget = ref('paused')
const contentChanges = ref<Record<string,unknown>>({})
const preview = ref(false), submitKey = ref('')
let timer: ReturnType<typeof setTimeout> | undefined
let sequence = 0
let disposed = false
const jobFilter = ref('')
const jobs = computed(() => page.value?.jobs.filter(j => !jobFilter.value || j.status === jobFilter.value) || [])
const price = computed(() => selected.value?.prices.find(p => p.id === scope.value))
const stock = computed(() => selected.value?.stocks.find(s => s.id === scope.value))
const hasActive = computed(() => page.value?.jobs.some(j => ['queued','running','submitted','waiting_confirmation'].includes(j.status)))
const activeSync = computed(() => page.value?.jobs.find(j => j.operation === 'sync' && ['queued', 'running'].includes(j.status)))
const stale = computed(() => !!page.value?.latest_sync && Date.now() - Date.parse(page.value.latest_sync.updated_at) > 3600000)
const saleState = computed(() => selected.value?.sale_state === 'paused' ? 'active' : 'paused')
const fieldNames: Record<string,string> = {preserved_pause:'保持主动停售',amount:'价格', currency:'币种', quantity:'绝对库存', title:'标题', description:'描述', pictures:'完整图片列表', attributes:'属性', state:'销售状态'}
const changes = computed<Record<string, unknown>>(() => {
  if (modal.value === 'price') return {amount: value.value, currency: price.value?.currency || ''}
  if (modal.value === 'stock') return {quantity: Number(value.value)}
  if (modal.value === 'sale_state') return {state: saleTarget.value}
  if (modal.value === 'content') return contentChanges.value
  return {}
})
function jobTarget(job: OnlineJob) {
  if (job.operation === 'sync') return Array.isArray(job.request.ids) ? `失败商品（${job.request.ids.length} 件）` : '全店商品'
  return job.result.before?.title || job.target_id
}
function showSync() {
  if (activeSync.value) { selectedJob.value = activeSync.value; showRecords.value = true }
  else begin('sync')
}
function syncProgress(job: OnlineJob) {
  const result = job.result
  const processed = (result.completed || 0) + (result.failed || 0)
  if (job.status === 'queued') return '同步已排队，可离开页面'
  if (job.status === 'running' && result.phase === 'catalog') return `正在读取商品目录，已发现 ${result.discovered || 0} 件`
  if (job.status === 'running' && result.phase === 'details') return result.discovery_complete
    ? `正在补齐详情，已处理 ${processed} / ${result.discovered || 0} 件`
    : `正在同步商品，已处理 ${processed} 件`
  return `已处理 ${processed} 件${result.discovery_complete === false ? '，目录尚未读取完整' : ''}`
}
function detailState(row: OnlineListing) {
  return row.details_state === 'pending' ? '详情同步中' : row.details_state === 'failed' ? '详情同步失败，已有快照保留' : ''
}
function canRetry(job: OnlineJob) {
  return ['failed', 'partial'].includes(job.status) && (job.operation === 'sync'
    ? job.result.items?.some(item => item.status === 'failed')
    : job.status === 'failed')
}
function label(s: string) { return labels[s] || s || '未知' }
function time(s: string) { return s ? new Date(s).toLocaleString('zh-CN', {hour12:false}) : '尚未同步' }
function badge(s: string) { return ['confirmed','active'].includes(s) ? 'badge-success' : ['failed','outcome_unknown'].includes(s) ? 'badge-warning' : 'badge-muted' }
function priceKind(s: string) { return ({net_proceeds:'净收入报价',base_price:'基础价',sale_price:'售价'} as Record<string,string>)[s] || s }
async function refresh(quiet = false) {
  if (disposed) return
  const ticket = ++sequence
  if (!quiet) loading.value = true
  try {
    const result = await fetchOnlineProducts({platform:platform.value, q:query.value, status:status.value, market:market.value, page:currentPage.value})
    if (ticket !== sequence) return
    if (page.value?.account_id !== result.account_id) expandedGroups.value.clear()
    page.value = result
    if (selected.value && !modal.value) selected.value = result.items.find(row => row.id === selected.value?.id) || selected.value
    error.value = ''
    if (selectedJob.value) selectedJob.value = result.jobs.find(j=>j.id===selectedJob.value?.id) || selectedJob.value
  } catch (e) { if(ticket === sequence) error.value = e instanceof Error ? e.message : '读取失败' }
  finally {
    if(ticket === sequence) loading.value = false
    if (!disposed && ticket === sequence) {
      clearTimeout(timer)
      timer = setTimeout(() => void refresh(true), hasActive.value ? 3000 : 15000)
    }
  }
}
async function detail(row: OnlineListing) {
  error.value = ''
  const epoch = viewEpoch
  try {
    const result = await fetchOnlineDetail(row.id)
    if (disposed || epoch !== viewEpoch || page.value?.account_id !== result.account_id) return
    const current = page.value.items.find(item => item.id === row.id)
    selected.value = current && current.status_checked_at > result.status_checked_at ? current : result
  } catch(e) {if (!disposed && epoch === viewEpoch) error.value = String(e)}
}
function statusRefreshBlocked(row: OnlineListing) {
  return checkingStatus.value.has(row.id) || !!modal.value || !!page.value?.jobs.some(job =>
    ['queued', 'running'].includes(job.status) && (job.target_id === '*' || job.target_id === row.id))
}
async function refreshStatus(row: OnlineListing) {
  if (statusRefreshBlocked(row)) return
  const epoch = viewEpoch
  checkingStatus.value.add(row.id)
  delete statusErrors.value[row.id]
  try {
    const result = await refreshOnlineStatus(row.id)
    if (disposed || epoch !== viewEpoch || page.value?.account_id !== result.account_id) return
    // 让刷新前发出的本地列表请求失效，避免旧响应覆盖刚查到的状态。
    sequence++
    page.value.items = page.value.items.map(item => item.id === result.id ? result : item)
    if (selected.value?.id === result.id) selected.value = result
    await refresh(true)
  } catch(e) {
    if (!disposed && epoch === viewEpoch && page.value?.account_id === row.account_id)
      statusErrors.value[row.id] = e instanceof Error ? e.message : '状态查询失败，原数据已保留'
  } finally { checkingStatus.value.delete(row.id) }
}
function begin(operation: OnlineOperation | 'sync') {
  modal.value = operation; preview.value = false; submitKey.value = crypto.randomUUID(); error.value = ''
  contentChanges.value = {}
  saleTarget.value = saleState.value
  scope.value = operation === 'price' ? selected.value?.prices.find(p=>p.writable)?.id || '' : operation === 'stock' ? selected.value?.stocks.find(s=>s.writable)?.id || '' : 'global'
  resetValue()
}
function resetValue() { value.value = modal.value === 'price' ? price.value?.amount || '' : String(stock.value?.quantity ?? '') }
function makePreview() {
  try {
    if(!Object.keys(changes.value).length) throw new Error('请至少选择一个变更字段')
    if (modal.value === 'stock' && (!value.value.trim() || !Number.isInteger(Number(value.value)) || Number(value.value)<0)) throw new Error('库存必须是非负整数')
    if (modal.value === 'price' && !(Number(value.value)>0)) throw new Error('请输入有效价格')
    preview.value = true; error.value = ''
  } catch(e) { error.value = e instanceof Error ? e.message : String(e) }
}
function beforeValue(field: string) {
  if(field==='amount') return price.value?.amount
  if(field==='currency') return price.value?.currency
  if(field==='quantity') return stock.value?.quantity
  if(field==='state') return selected.value?.sale_state
  return selected.value?.content[field as keyof OnlineListing['content']]
}
function displayValue(field: string, data: unknown): string {
  if(field==='state') return label(String(data || ''))
  if(field==='pictures' && Array.isArray(data)) return `${data.length} 张图片（按显示顺序提交）`
  if(field==='attributes' && Array.isArray(data)) {
    const ids = new Set((changes.value.attributes as Array<{id:unknown}> || []).map(a=>String(a.id)))
    return data.filter(a=>ids.has(String(a.id))).map(a=>{
      const name=selected.value?.content.attributes?.find(row=>String(row.id)===String(a.id))?.name || a.parameterName || a.id
      return `${name}：${a.value_name ?? a.value ?? a.values?.map((v:{name?:string})=>v.name).join('、') ?? '未知'}`
    }).join('；')
  }
  return data == null ? '未提供' : String(data)
}
function pictureUrl(p: string | {url:string}) { return typeof p==='string' ? p : p.url }
async function submit() {
  busy.value = true; error.value = ''
  try {
    const body = modal.value === 'sync' ? {platform: platform.value, idempotency_key: submitKey.value} : {listing_id: selected.value!.id, version: selected.value!.version, operation: modal.value, scope_id: scope.value, changes: changes.value, idempotency_key: submitKey.value}
    const job = await onlineAction(modal.value === 'sync' ? 'sync' : 'change', body)
    modal.value = ''; selected.value = null; selectedJob.value = job; showRecords.value = true
    notice.value = job.operation === 'sync' ? '同步已排队，可离开页面。商品目录与详情进度会持续更新。' : '任务已排队，可离开页面。修改是否生效以平台回读结果为准。'
    await refresh(true)
  } catch(e) {error.value = e instanceof Error ? e.message : '提交失败'} finally {busy.value=false}
}
async function jobAction(action: 'reconcile' | 'retry', job: OnlineJob) {
  busy.value = true; error.value = ''
  try { selectedJob.value = await onlineAction(action, {job_id: job.id, ...(action==='retry' ? {idempotency_key:crypto.randomUUID()} : {})}); await refresh(true) }
  catch(e) {error.value = e instanceof Error ? e.message : '操作失败'} finally {busy.value=false}
}
function filter() {currentPage.value=1; void refresh()}
watch(platform, () => { viewEpoch++; statusErrors.value={}; modal.value=''; notice.value=''; page.value=null; selected.value=null; selectedJob.value=null; expandedGroups.value.clear(); status.value=market.value=''; filter() })
onMounted(() => void refresh())
onBeforeUnmount(() => {disposed=true; sequence++; clearTimeout(timer)})
</script>

<template>
  <section class="online-products space-y-6">
    <header class="flex flex-wrap items-start justify-between gap-4">
      <div><h1 class="text-2xl font-bold">{{ showRecords ? '操作记录' : '在线商品' }}</h1><p class="muted mt-2">{{ showRecords ? '查看同步与商品修改进度，按实际结果处理失败和待确认项。' : '同步店铺已有商品，集中管理销售状态、价格、库存与内容。' }}</p></div>
      <div class="flex gap-3"><button class="btn btn-outline" @click="showRecords=!showRecords">{{ showRecords ? '返回在线商品' : '操作记录' }}</button><button v-if="!showRecords" class="btn btn-primary" :disabled="busy || !page?.account_id" @click="showSync">{{ activeSync ? '查看同步进度' : '↻ 同步商品' }}</button></div>
    </header>
    <div class="flex flex-wrap items-center justify-between gap-4"><div class="flex gap-2" role="tablist" aria-label="平台"><button v-for="(name,key) in platforms" :key="key" role="tab" :aria-selected="platform===key" class="btn" :class="platform===key?'btn-primary':'btn-outline'" @click="platform=key">{{ name }}</button></div><span class="input w-auto">{{ page?.store_name || '尚未连接店铺' }}</span></div>
    <p v-if="error" role="alert" class="rounded-lg bg-rose-50 p-4 text-rose-700">{{ error }} <button class="underline" @click="refresh()">重新读取</button></p>
    <p v-if="notice" role="status" class="rounded-lg bg-primary-50 p-4 text-primary-800">{{ notice }}</p>
    <p v-if="stale" class="rounded-lg bg-amber-50 p-3 text-amber-800">价格、库存和内容快照已超过一小时，可同步更新；商品状态可单独刷新。</p>
    <template v-if="!showRecords">
      <div class="grid grid-cols-2 gap-4 lg:grid-cols-4"><article v-for="(count,key) in page?.summary || {total:0,active:0,paused:0,attention:0}" :key="key" class="online-card p-4"><p class="muted">{{ {total:'全部刊登 / SKU',active:'在售',paused:'已停售',attention:'需关注'}[key] }}</p><strong class="mt-2 block text-2xl" :class="key==='active'?'text-primary-700':key==='attention'?'text-amber-700':''">{{ count }}</strong></article></div>
      <div v-if="page?.latest_sync" class="online-card flex flex-wrap items-center justify-between gap-3 p-4"><div><b>最近同步 · {{ jobTarget(page.latest_sync) }} · {{ page.latest_sync.status==='confirmed'?'同步完成':label(page.latest_sync.status) }}</b><p class="mt-1" role="status">{{ syncProgress(page.latest_sync) }}</p><p class="muted mt-1">成功 {{ page.latest_sync.result.completed || 0 }} · 新增 {{ page.latest_sync.result.created || 0 }} · 更新 {{ page.latest_sync.result.updated || 0 }} · 失败 {{ page.latest_sync.result.failed || 0 }}</p><p v-if="page.latest_sync.result.error" class="text-sm text-rose-600">{{ page.latest_sync.result.error }}</p></div><button class="text-primary-700" @click="selectedJob=page.latest_sync; showRecords=true">查看结果 →</button></div>
      <form class="flex flex-wrap items-center gap-3" @submit.prevent="filter"><input v-model="query" class="input w-full md:max-w-sm" placeholder="搜索标题、卖家 SKU 或平台商品 ID" aria-label="搜索商品" /><select v-model="status" class="input w-auto" aria-label="销售状态" @change="filter"><option value="">全部状态</option><option v-for="s in page?.statuses" :key="s" :value="s">{{ label(s) }}</option></select><select v-model="market" class="input w-auto" aria-label="销售市场" @change="filter"><option value="">全部市场</option><option v-for="s in page?.markets" :key="s">{{ s }}</option></select><button class="btn btn-outline" type="submit">搜索</button><span class="muted ml-auto">最近同步：{{ time(page?.latest_sync?.updated_at || '') }}</span></form>
      <div class="online-card overflow-x-auto" :aria-busy="loading">
        <table class="w-full min-w-[950px] text-left text-sm">
          <thead><tr><th>商品</th><th>市场 / 价格</th><th>库存</th><th>销售状态</th><th>最近同步</th><th>操作</th></tr></thead>
          <tbody v-for="group in listingGroups" :key="group.id">
            <tr v-if="group.kind === 'group'" class="online-group" data-testid="online-group">
              <td>
                <button type="button" class="group flex max-w-sm items-center gap-3 rounded-lg text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 focus-visible:ring-offset-4" data-testid="toggle-group" :aria-expanded="expandedGroups.has(group.id)" :aria-label="`${expandedGroups.has(group.id) ? '收起' : '展开'}组合商品：${group.title}`" @click="toggleGroup(group.id)">
                  <span aria-hidden="true" class="flex size-11 shrink-0 items-center justify-center rounded-lg border border-primary-200 bg-white text-primary-700 transition-colors group-hover:border-primary-400 group-hover:bg-primary-100 dark:border-primary-700 dark:bg-dark-800 dark:text-primary-300 dark:group-hover:bg-primary-900/40">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" class="size-6 transition-transform motion-reduce:transition-none" :class="{'rotate-90': expandedGroups.has(group.id)}"><path d="m9 5 7 7-7 7" /></svg>
                  </span>
                  <img v-if="group.rows[0]?.thumbnail" :src="group.rows[0].thumbnail" referrerpolicy="no-referrer" alt="" class="size-12 shrink-0 rounded-lg object-cover" />
                  <span v-else class="size-12 shrink-0 rounded-lg bg-accent-100" />
                  <span class="min-w-0"><span class="line-clamp-2 font-semibold">{{ group.title }}</span><span class="mt-1 block text-xs text-primary-700">组合商品 · {{ group.total_count }} 个 SKU</span></span>
                </button>
              </td>
              <td colspan="5">
                <p v-if="group.rows.length < group.total_count" class="font-medium">当前筛选匹配 {{ group.rows.length }} / {{ group.total_count }} 个 SKU</p>
                <p v-else class="font-medium">{{ group.rows.length }} 个 SKU 统一展示</p>
                <p class="muted mt-1">展开查看各 SKU 的价格、库存和销售状态</p>
              </td>
            </tr>
            <template v-if="group.kind === 'single' || expandedGroups.has(group.id)">
              <tr v-for="row in group.rows" :key="row.id" data-testid="online-listing" :class="{'online-child': group.kind === 'group'}">
                <td><div class="flex max-w-sm items-center gap-3" :class="{'ml-7 border-l-2 border-primary-200 pl-4': group.kind === 'group'}"><img v-if="row.thumbnail" referrerpolicy="no-referrer" :src="row.thumbnail" alt="" class="size-12 shrink-0 rounded-lg object-cover" /><div v-else class="size-12 shrink-0 rounded-lg bg-accent-100" /><div><p class="line-clamp-2 font-medium">{{ row.title || row.remote_id }}</p><p class="muted mt-1 break-all">{{ row.seller_sku || row.remote_id }}</p></div></div></td>
                <td><p v-if="row.prices[0]">{{ row.prices[0].currency }} {{ row.prices[0].amount ?? '未知' }}<span v-if="row.prices.length>1"> · {{ row.prices.length }} 个价格范围</span></p><p v-else>尚未取得价格</p><p class="muted mt-1">{{ row.prices[0] ? priceKind(row.prices[0].kind) : '' }} · {{ [...new Set(row.markets.map(m=>m.site_id))].join(' · ') }}</p></td>
                <td><p>{{ row.stocks[0]?.quantity ?? '未知' }}<span v-if="row.stocks.length>1"> 等 {{ row.stocks.length }} 个范围</span></p><p class="muted mt-1">{{ row.stocks[0]?.label || '未取得库存范围' }}</p></td>
                <td><span :class="badge(row.sale_state)">{{ row.sale_state==='paused'?'已停售':label(row.raw_status) }}</span><p v-if="detailState(row)" class="mt-1 text-amber-700">{{ detailState(row) }}</p><p v-else-if="row.errors.length" class="mt-1 text-amber-700">读取不完整</p></td>
                <td class="muted whitespace-nowrap"><p>{{ time(row.synced_at) }}</p><p v-if="row.status_checked_at" class="mt-1 text-xs">状态查询：{{ time(row.status_checked_at) }}</p></td>
                <td><div class="flex flex-col items-start gap-2"><button class="whitespace-nowrap text-primary-700" @click="detail(row)">管理 →</button><button type="button" data-testid="refresh-status" class="whitespace-nowrap text-primary-700 disabled:opacity-50" :disabled="statusRefreshBlocked(row)" @click="refreshStatus(row)">{{ checkingStatus.has(row.id) ? '查询中…' : '刷新状态' }}</button><p v-if="statusErrors[row.id]" role="alert" class="max-w-64 text-xs text-rose-700">{{ statusErrors[row.id] }}</p><OnlineBuyerLinks :links="row.buyer_links" /></div></td>
              </tr>
            </template>
          </tbody>
        </table>
        <div v-if="!page?.items.length" class="p-14 text-center"><p class="text-lg font-semibold">{{ loading?'正在读取在线商品…':page?.state==='authorization_required'?'请先连接并验证店铺':page?.state==='never_synced'?'还没有同步店铺商品':page?.latest_sync?.status==='confirmed' && page.summary.total===0?'店铺同步完成，暂无商品':query || status || market?'当前筛选无结果':'暂未取得商品，请查看同步结果' }}</p><p class="muted mt-3">同步会读取店铺已有刊登，包括未通过本 ERP 发布的商品。</p></div>
        <footer class="flex items-center justify-between border-t p-4"><span class="muted">共 {{ page?.total || 0 }} 个商品节点 · {{ page?.listing_total || 0 }} 条刊登 / SKU，第 {{ currentPage }} 页</span><div class="flex gap-2"><button class="btn btn-outline" :disabled="currentPage<=1 || loading" @click="currentPage--; refresh()">上一页</button><button class="btn btn-outline" :disabled="currentPage*(page?.per_page || 25) >= (page?.total || 0) || loading" @click="currentPage++; refresh()">下一页</button></div></footer>
      </div><p class="muted">数据来自最近一次平台同步；修改提交后，以平台回读结果确认生效。</p>
    </template>
    <template v-else>
      <select v-model="jobFilter" class="input w-auto" aria-label="操作状态"><option value="">全部状态</option><option v-for="s in ['queued','running','waiting_confirmation','confirmed','partial','failed','outcome_unknown']" :key="s" :value="s">{{ label(s) }}</option></select>
      <div class="online-card overflow-x-auto"><table class="w-full text-left text-sm"><thead><tr><th>操作 / 商品</th><th>平台</th><th>状态</th><th>提交时间</th><th>处理</th></tr></thead><tbody><tr v-for="job in jobs" :key="job.id"><td><b>{{ label(job.operation) }}</b><p class="muted mt-1">{{ jobTarget(job) }}</p></td><td>{{ platforms[job.platform] }}</td><td><span :class="badge(job.status)">{{ label(job.status) }}</span></td><td>{{ time(job.created_at) }}</td><td><button class="text-primary-700" @click="selectedJob=job">查看详情</button></td></tr></tbody></table><p v-if="!jobs.length" class="muted p-10 text-center">暂无操作记录</p></div>
      <p v-if="selectedJob?.operation!=='sync'" class="rounded-lg bg-amber-50 p-5 text-amber-800">结果未确认时，不重复提交。网络超时后平台可能已完成修改，先查询处理结果。</p>
      <article v-if="selectedJob" class="online-card space-y-4 p-6"><h2 class="text-lg font-semibold">{{ label(selectedJob.operation) }} · {{ selectedJob.operation==='sync' && selectedJob.status==='confirmed'?'同步完成':label(selectedJob.status) }}</h2><p v-if="selectedJob.operation==='sync'" role="status">{{ syncProgress(selectedJob) }} · 成功 {{ selectedJob.result.completed || 0 }} · 失败 {{ selectedJob.result.failed || 0 }}</p><p v-if="selectedJob.result.error" role="alert" class="text-rose-600">{{ selectedJob.result.error }}</p><p v-if="selectedJob.result.evidence?.synced_at" class="text-primary-700">平台回读：{{ time(selectedJob.result.evidence.synced_at) }}</p><p v-for="(issue,index) in selectedJob.result.platform_errors || []" :key="index" class="text-rose-600">{{ issue }}</p><p v-if="selectedJob.result.platform_confirmation?.note" class="text-amber-700">{{ selectedJob.result.platform_confirmation.note }}</p><dl v-if="selectedJob.result.confirmation" class="space-y-2"><div v-for="(ok,field) in selectedJob.result.confirmation" :key="field">{{ fieldNames[field] || field }}：{{ ok?'回读一致':'尚未确认一致' }}</div></dl><ul class="max-h-80 space-y-2 overflow-auto"><li v-for="item in selectedJob.result.items?.filter(i=>i.status==='failed')" :key="item.remote_id" class="text-sm">{{ item.remote_id }}：{{ item.error }}</li></ul><div class="flex gap-3"><button v-if="['submitted','waiting_confirmation','outcome_unknown'].includes(selectedJob.status)" class="btn btn-primary" :disabled="busy" @click="jobAction('reconcile',selectedJob)">查询平台结果</button><button v-if="canRetry(selectedJob)" class="btn btn-outline" :disabled="busy" @click="jobAction('retry',selectedJob)">仅重试失败项</button></div></article>
    </template>

    <Teleport to="body">
      <div v-if="selected" class="online-overlay" @pointerdown="detailBackdrop.recordBackdropPointer" @pointerup="detailBackdrop.dismissFromBackdrop" @pointercancel="detailBackdrop.resetBackdropPointer"><aside role="dialog" aria-modal="true" aria-label="商品详情" class="online-drawer"><div class="flex justify-between"><h2 class="text-xl font-bold">商品详情</h2><button class="text-primary-700" @click="selected=null">关闭 ×</button></div><div class="my-8 flex items-center gap-4"><img v-if="selected.thumbnail" referrerpolicy="no-referrer" :src="selected.thumbnail" alt="" class="size-20 rounded-lg" /><div><h3 class="text-lg font-semibold">{{ selected.title }}</h3><p class="muted mt-2">SKU {{ selected.seller_sku || '未提供' }} · {{ platforms[selected.platform] }}</p></div></div><div class="flex flex-wrap items-center gap-4"><span :class="badge(selected.raw_status)">{{ label(selected.raw_status) }}</span><span class="muted">最近同步：{{ time(selected.synced_at) }}</span><OnlineBuyerLinks :links="selected.buyer_links" /><button type="button" data-testid="detail-refresh-status" class="btn btn-outline" :disabled="statusRefreshBlocked(selected)" @click="refreshStatus(selected)">{{ checkingStatus.has(selected.id) ? '查询中…' : '刷新状态' }}</button></div><p class="muted mt-3">状态查询：{{ selected.status_checked_at ? time(selected.status_checked_at) : '尚未查询' }} · 仅更新本商品状态，价格、库存和内容保留最近同步结果。</p><p v-if="statusErrors[selected.id]" role="alert" class="mt-3 text-rose-700">{{ statusErrors[selected.id] }}</p><p class="muted mt-3">平台原始状态：{{ selected.raw_status }} {{ selected.raw_sub_status.join('、') }}</p><p v-if="detailState(selected)" class="mt-3 text-amber-700">{{ detailState(selected) }}，完成后可修改。</p><p v-for="e in selected.errors" :key="e" class="mt-3 text-sm text-rose-700">{{ e }}</p><div class="my-6 grid grid-cols-2 gap-3 md:grid-cols-4"><button v-for="op in ['price','stock','content','sale_state'] as const" :key="op" class="btn btn-outline" :disabled="checkingStatus.has(selected.id) || selected.details_state!=='ready' || !!selected.errors.length || !selected.capabilities[op]?.enabled" :title="selected.capabilities[op]?.reason" @click="begin(op)">{{ op==='sale_state'?(saleState==='active'?'恢复销售':'停售商品'):label(op) }}</button></div><p v-for="(cap,op) in selected.capabilities" v-show="cap.reason" :key="op" class="muted mb-2">{{ label(op) }}：{{ cap.reason }}</p><h3 class="mt-8 font-semibold">销售市场</h3><div class="online-card my-5 overflow-x-auto"><table class="w-full text-left text-sm"><thead><tr><th>销售市场</th><th>价格 / 币种</th><th>销售状态</th></tr></thead><tbody><tr v-for="m in selected.markets" :key="m.id"><td>{{ m.site_id }} · {{ m.logistic_type }}<p class="muted mt-1">{{ m.id }}</p></td><td>{{ m.currency }} {{ m.price ?? '未知' }}</td><td>{{ label(m.raw_status) }}</td></tr></tbody></table></div><div v-for="s in selected.stocks" :key="s.id" class="my-3 rounded-lg bg-primary-50 p-5 text-primary-800"><b>{{ s.label }}：{{ s.quantity ?? '未知' }}</b><p class="mt-2 text-sm">{{ s.reason || '设置绝对数量，仅影响这个库存范围。' }}</p></div><h3 class="mt-8 font-semibold">平台身份</h3><p class="muted mt-3 break-all">{{ selected.remote_id }} · {{ selected.model }}</p><p class="muted mt-2">来源：店铺同步 · 账号 {{ selected.account_id }}</p></aside></div>
      <div v-if="modal" class="online-overlay online-modal-layer" @pointerdown="modalBackdrop.recordBackdropPointer" @pointerup="modalBackdrop.dismissFromBackdrop" @pointercancel="modalBackdrop.resetBackdropPointer">
        <section role="dialog" aria-modal="true" :aria-label="modal==='sync'?'同步商品':label(modal)" class="online-modal space-y-5" :class="modal==='content' && !preview ? 'online-content-form' : ''">
          <header class="flex justify-between"><h2 class="text-xl font-bold">{{ modal==='sync'?'同步商品':preview?'确认本次变更':label(modal) }}</h2><button :disabled="busy" @click="modal=''">关闭 ×</button></header><p v-if="error" role="alert" class="text-rose-700">{{ error }}</p>
          <template v-if="modal==='sync'"><p>同步 {{ platforms[platform] }} 店铺：{{ page?.store_name }}</p><div class="rounded-lg bg-accent-50 p-5"><b>同步范围</b><p class="mt-3">当前账号全部可读取商品、所有关联市场及在售、停售、缺货、审核中和归档记录。</p></div><p class="muted">此操作只读取平台商品。部分失败会保留已有快照；离开页面后后台继续同步。</p><button class="btn btn-primary w-full" :disabled="busy" @click="submit">开始同步</button></template>
          <template v-else-if="selected">
            <p class="font-medium">{{ selected.title }}</p><p class="muted">作用范围：{{ modal==='price' ? price?.label : modal==='stock' ? stock?.label : selected.capabilities[modal]?.scope }}</p>
            <template v-if="!preview">
              <template v-if="modal==='price'"><label class="block">价格范围<select v-model="scope" class="input mt-2" @change="resetValue"><option v-for="p in selected.prices" :key="p.id" :value="p.id" :disabled="!p.writable">{{ p.label }} · {{ priceKind(p.kind) }} · {{ p.currency }}</option></select></label><p class="muted">当前：{{ price?.currency }} {{ price?.amount }} · {{ priceKind(price?.kind || '') }}</p><label class="block">新价格（{{ price?.currency }}）<input v-model="value" class="input mt-2" inputmode="decimal" /></label></template>
              <template v-if="modal==='stock'"><label class="block">库存范围<select v-model="scope" class="input mt-2" @change="resetValue"><option v-for="s in selected.stocks" :key="s.id" :value="s.id" :disabled="!s.writable">{{ s.label }}</option></select></label><p class="muted">当前库存：{{ stock?.quantity }}。设置绝对数量，不是增减量。</p><label class="block">目标可售数量<input v-model="value" type="number" min="0" step="1" class="input mt-2" /></label></template>
              <template v-if="modal==='sale_state'"><label class="block">目标销售状态<select v-model="saleTarget" class="input mt-2"><option value="paused">停售（已暂停时可重新确认主动停售）</option><option value="active">恢复销售</option></select></label><p class="rounded-lg bg-amber-50 p-5 text-amber-800">{{ saleTarget==='paused'?'主动停售会影响上述范围内的销售市场，库存同步不会取消停售意图。':'恢复请求仍须满足平台库存、审核及账号限制；提交成功不代表已经恢复在售。' }}</p><p>{{ selected.markets.map(m=>`${m.site_id} · ${m.logistic_type}`).join('、') }}</p></template>
              <OnlineContentEditor v-if="modal==='content'" :key="selected.id" :listing="selected" @change="contentChanges=$event" />
              <button class="btn btn-primary w-full" @click="makePreview">预览变更</button>
            </template>
            <template v-else><div v-for="(next,field) in changes" :key="field" class="rounded-lg border p-4"><b>{{ fieldNames[field] || field }}</b><p class="muted mt-2 break-all">原：{{ displayValue(field, beforeValue(field)) }}</p><p class="mt-2 break-all text-primary-700">新：{{ displayValue(field, next) }}</p><div v-if="field==='pictures'" class="mt-3 flex flex-wrap gap-2"><img v-for="(p,index) in next as Array<string | {url:string}>" :key="index" referrerpolicy="no-referrer" :src="pictureUrl(p)" :alt="`目标图片 ${index+1}`" class="size-16 rounded border object-contain" /></div></div><p class="muted">提交前将重新读取平台字段；发生并发变化时会停止修改。</p><div class="flex gap-3"><button class="btn btn-outline" :disabled="busy" @click="preview=false">返回编辑</button><button class="btn btn-primary flex-1" :disabled="busy" @click="submit">{{ busy?'正在提交…':'确认提交' }}</button></div></template>
          </template>
        </section>
      </div>
    </Teleport>
  </section>
</template>

<style scoped>
.online-card { @apply rounded-lg border border-accent-200 bg-white dark:border-dark-700 dark:bg-dark-900; }
th { @apply bg-accent-50 px-5 py-3 text-xs font-normal text-accent-500 dark:bg-dark-950; }
td { @apply border-t border-accent-200 px-5 py-4 align-middle dark:border-dark-700; }
.online-group { @apply bg-primary-50/60 dark:bg-primary-900/10; }
.online-child { @apply bg-accent-50/30 dark:bg-dark-950/30; }
.online-overlay { @apply fixed inset-0 z-50 flex justify-end bg-black/30; }
.online-drawer { @apply h-full w-full max-w-[760px] overflow-y-auto bg-white p-8 shadow-xl dark:bg-dark-900; }
.online-modal-layer { @apply z-[60] items-center justify-center p-4; }
.online-modal { @apply max-h-[90vh] w-full max-w-[640px] overflow-y-auto rounded-xl bg-white p-8 shadow-xl dark:bg-dark-900; }
.online-content-form { @apply max-w-[1000px]; }
</style>
