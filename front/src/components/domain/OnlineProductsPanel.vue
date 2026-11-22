<script setup lang="ts">
import { formatYandexWarranty } from '@/utils/yandexWarranty'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { onClickOutside } from '@vueuse/core'
import {
  PhArrowsClockwise,
  PhCaretDown,
  PhCaretRight,
  PhCheckCircle,
  PhClockCountdown,
  PhImage,
  PhLightning,
  PhListChecks,
  PhMagnifyingGlass,
  PhWarningCircle,
  PhX,
} from '@phosphor-icons/vue'
import OzonExportDialog from './OzonExportDialog.vue'
import OnlineContentEditor from './OnlineContentEditor.vue'
import OnlinePicturePreview from './OnlinePicturePreview.vue'
import OnlinePriceCell from './OnlinePriceCell.vue'
import OnlineBuyerLinks from './OnlineBuyerLinks.vue'
import OnlineProductDetails from './OnlineProductDetails.vue'
import SkuSourceManagerDialog from './SkuSourceManagerDialog.vue'
import WorkspaceDialog from '@/components/shared/WorkspaceDialog.vue'
import PageHeader from '@/components/layout/PageHeader.vue'
import OnlinePlatformFeedback from './OnlinePlatformFeedback.vue'
import { onlineAttributeName } from './onlineAttributeDisplay'
import { useAiPageContext } from '@/composables/useAiPageContext'
import { useBackdropDismiss } from '@/composables/useBackdropDismiss'
import type { SalesSkuBinding } from '@/types/orders'
import { fetchYandexShopNames, refreshOnlineSales, fetchOnlineDetail, fetchOnlineProducts, onlineAction, previewOnlineBatch, submitOnlineBatch, type OnlineBatchPreview, refreshOnlineDetail, refreshOnlineStatus, type OnlineJob, type OnlineListing, type OnlineOperation, type OnlinePage, type OnlinePlatform } from '@/api/onlineProducts'

const emit = defineEmits<{ 'open-task': [jobId?: string] }>()

const platform = ref<OnlinePlatform>('mercadolibre')
const catalog = ref<'current' | 'archived'>('current')
const warehouse = ref('')
const syncCatalog = ref<'current' | 'archived'>('current')
const syncCatalogLabel = computed(() => syncCatalog.value === 'archived' ? '归档商品' : '普通商品')
const platforms: Record<OnlinePlatform, string> = {mercadolibre: 'Mercado Libre', ozon: 'Ozon', yandex: 'Yandex Market'}
const labels: Record<string, string> = {active: '在售', paused: '已停售', closed: '已关闭', under_review: '审核中', queued: '排队中', running: '执行中', submitted: '平台已受理', waiting_confirmation: '等待平台确认', confirmed: '已完成', PUBLISHED: '在售', CHECKING: '审核中', CREATING_CARD: '建卡中', NO_CARD: '待补充商品信息', REJECTED_BY_MARKET: '平台拒绝', DISABLED_AUTOMATICALLY: '平台停售', DISABLED_BY_PARTNER: '已停售', NO_STOCKS: '缺货', DISABLED: '不可售', ARCHIVED: '已归档', HAS_CARD_CAN_UPDATE: '卡片可更新', HAS_CARD_CAN_NOT_UPDATE: '平台卡片只读', HAS_CARD_CAN_UPDATE_ERRORS: '卡片修改未被接受', HAS_CARD_CAN_UPDATE_PROCESSING: '卡片修改处理中', NO_CARD_NEED_CONTENT: '等待补充卡片内容', NO_CARD_MARKET_WILL_CREATE: '平台将创建卡片', NO_CARD_ERRORS: '卡片创建失败', NO_CARD_PROCESSING: '卡片创建处理中', NO_CARD_ADD_TO_CAMPAIGN: '等待添加到店铺', partial: '部分成功', failed: '失败', outcome_unknown: '结果未知', needs_attention: '需要处理', closed_unconfirmed: '已结束，结果未确认', sync: '同步店铺商品', price: '调整价格', stock: '修改库存', content: '编辑内容', sale_state: '停售 / 恢复'}
const page = ref<OnlinePage | null>(null)
const shopNames = ref<Record<string, string>>({})
watch(() => page.value?.account_id, async (account, _, onCleanup) => {
  shopNames.value = {}
  if (!account || platform.value !== 'yandex') return
  let current = true
  onCleanup(() => { current = false })
  try {
    const names = await fetchYandexShopNames(account)
    if (current) shopNames.value = names
  } catch {
    // 店名不可用时仍可查看已同步的价格。
  }
})
const checkedListings = ref(new Set<string>())
const actionMenuOpen = ref(false), actionMenu = ref<HTMLElement | null>(null)
const showOzonExport = ref(false)
const exportListingIds = ref<string[]>([])
const checkedGroups = ref(new Set<string>())
const batchSelection = ref<{preview:OnlineBatchPreview; operation:'sale_state'|'archive'; changes:Record<string,unknown>; title:string; key:string; scope_id:string} | null>(null)
const batchError = ref('')
const selectedRows = computed(() => (page.value?.items || []).filter(row=>checkedListings.value.has(row.id)))
const canArchiveSelection = computed(() => selectedRows.value.length > 0 && selectedRows.value.every(row=>row.capabilities.archive?.enabled))
const selectedWarehouse = computed(() => page.value?.warehouses?.find(item=>item.id===warehouse.value))
const canSaleSelection = computed(() => (!page.value?.warehouses?.length || !!selectedWarehouse.value) && selectedRows.value.length > 0 && selectedRows.value.every(row=>!row.archived && row.details_state==='ready' && !row.errors.length && row.capabilities.sale_state?.enabled))
async function beginSelection(operation:'sale_state'|'archive', target:boolean | 'paused' | 'active') {
  if (busy.value) return
  const changes = operation==='archive' ? {archived:target} : {state:target}
  const scope_id = operation==='archive' ? 'business' : selectedWarehouse.value ? `campaign:${selectedWarehouse.value.campaign_id}` : 'global'
  const title = operation==='archive' ? (target ? '归档所选商品' : '恢复所选归档') : target==='paused' ? '停售所选商品' : '恢复所选商品销售'
  busy.value = true; error.value=''; batchError.value=''; actionMenuOpen.value=false
  try {
    const result = await previewOnlineBatch({platform:platform.value,catalog:catalog.value,
      group_ids:[...checkedGroups.value],listing_ids:[...checkedListings.value],operation,changes,scope_id})
    batchSelection.value = {preview:result,operation,changes,title,key:crypto.randomUUID(),scope_id}
  } catch (cause) { error.value = cause instanceof Error ? cause.message : '读取所选商品失败' }
  finally { busy.value=false }
}
async function submitSelection() {
  const batch = batchSelection.value
  if (!batch || busy.value || batch.preview.errors.length) return
  busy.value = true; batchError.value=''
  try {
    const jobs = await submitOnlineBatch({items:batch.preview.items.map(({listing_id,version})=>({listing_id,version})),
      operation:batch.operation,changes:batch.changes,scope_id:batch.scope_id,idempotency_key:batch.key})
    notice.value = `已提交 ${batch.preview.product_count} 个商品、${jobs.length} 个 SKU 的${batch.title.replace('所选商品','').replace('所选','')}请求，处理结果可在任务队列查看。`
    noticeTaskId.value = jobs[0]?.id || ''
    batchSelection.value=null
    clearSelection()
    await refresh(true)
  } catch (cause) { batchError.value = cause instanceof Error ? cause.message : '提交失败，请重试' }
  finally { busy.value=false }
}
onClickOutside(actionMenu, () => { actionMenuOpen.value = false })
function toggleListings(ids: string[], checked: boolean, groupId?: string) {
  if (groupId && checked) checkedGroups.value.add(groupId)
  if (!checked) for (const group of page.value?.groups || []) {
    if (group.item_ids.some(id=>ids.includes(id))) checkedGroups.value.delete(group.id)
  }
  for (const id of ids) {
    if (checked) checkedListings.value.add(id)
    else checkedListings.value.delete(id)
  }
}
function groupChecked(ids: string[]) { return ids.length > 0 && ids.every(id => checkedListings.value.has(id)) }
function groupPartial(ids: string[]) { return ids.some(id => checkedListings.value.has(id)) && !groupChecked(ids) }
function clearSelection() { checkedListings.value.clear(); checkedGroups.value.clear(); actionMenuOpen.value = false; showOzonExport.value = false }
function beginOzonExport() {
  if (!checkedListings.value.size) return
  exportListingIds.value = [...checkedListings.value]; actionMenuOpen.value = false; showOzonExport.value = true
}
const expandedGroups = ref(new Set<string>())
// 转到聊天输入框后仍保留最近操作的商品，避免发送时丢失指代。
const focusedProduct = ref<{group_id: string; listing_id?: string} | null>(null)
const listingGroups = computed(() => {
  const items = new Map(page.value?.items.map(row => [row.id, row]))
  return page.value?.groups.map(group => {
    const rows = group.item_ids.flatMap(id => { const row = items.get(id); return row ? [row] : [] })
    const sales = platform.value === 'yandex' && group.kind === 'group' ? groupSales(rows) : null
    return {...group, rows, sales}
  }) || []
})
function groupSales(rows: OnlineListing[]) {
  const ranges = new Map<string, {min: number; max: number}>()
  let quantity = 0, known = 0, partial = false, cached = false, missingPrice = false
  for (const row of rows) {
    let priced = false
    for (const price of row.prices) {
      if (!price.currency || !price.amount?.trim()) continue
      const amount = Number(price.amount)
      if (!Number.isFinite(amount)) continue
      priced = true
      const range = ranges.get(price.currency) || {min: amount, max: amount}
      ranges.set(price.currency, {min: Math.min(range.min, amount), max: Math.max(range.max, amount)})
    }
    const stock = stockSummary(row)
    if (stock.quantity !== null) { quantity += stock.quantity; known++ }
    partial ||= stock.partial || stock.quantity === null
    missingPrice ||= !priced
    cached ||= cachedDetails(row) || [...row.prices, ...row.stocks].some(scope => !!scope.cached_at)
  }
  const prices = [...ranges].map(([currency, range]) =>
    range.min === range.max ? `${currency} ${range.min}` : `${currency} ${range.min}–${range.max}`)
  return {prices, missingPrice, quantity: known ? quantity : null, partial: known > 0 && partial, cached}
}
function toggleGroup(id: string) {
  focusedProduct.value = {group_id: id}
  if (expandedGroups.value.has(id)) expandedGroups.value.delete(id)
  else expandedGroups.value.add(id)
}
const query = ref(''), status = ref(''), market = ref(''), stockState = ref(''), issueState = ref(''), priceState = ref(''), groupKind = ref(''), currentPage = ref(1)
const loading = ref(false), busy = ref(false), error = ref(''), notice = ref('')
const noticeTaskId = ref('')
const conflictJobId = ref('')
watch(error, message => { if (!message) conflictJobId.value = '' }, {flush:'sync'})
const checkingStatus = ref(new Set<string>())
const statusErrors = ref<Record<string, string>>({})
let viewEpoch = 0
const selected = ref<OnlineListing | null>(null)
const detailLoading = ref(false), detailError = ref('')
let detailSequence = 0
const modal = ref<OnlineOperation | 'sync' | ''>('')
const skuSourceTarget = ref<OnlineListing | null>(null)
const skuBindingsByListing = ref<Record<string, SalesSkuBinding[]>>({})
const detailBackdrop = useBackdropDismiss(() => { if (!modal.value && !skuSourceTarget.value) selected.value = null })
const modalBackdrop = useBackdropDismiss(() => { if (!busy.value) modal.value = '' })
watch(selected, detailBackdrop.resetBackdropPointer)
watch(modal, modalBackdrop.resetBackdropPointer)
function listingSkuBindings(row: OnlineListing): SalesSkuBinding[] | undefined {
  return skuBindingsByListing.value[row.id]
}
function listingDefaultSource(row: OnlineListing): SalesSkuBinding | null {
  const list = listingSkuBindings(row) || []
  return list.find(item => item.is_default) || (list.length === 1 ? list[0] : list[0] || null)
}
function handleOnlineSkuSourcesUpdated(row: OnlineListing, payload: { bindings: SalesSkuBinding[] }) {
  skuBindingsByListing.value = { ...skuBindingsByListing.value, [row.id]: payload.bindings }
}
useAiPageContext(() => ({
  page: 'onlineProducts', platform: platform.value, catalog: catalog.value,
  group_id: selected.value
    ? page.value?.groups.find(group => group.item_ids.includes(selected.value!.id))?.id
    : focusedProduct.value?.group_id,
  listing_id: selected.value?.id || focusedProduct.value?.listing_id,
}), 10)
const scope = ref('')
// 数字输入框的 v-model 会产生 number，清空输入后仍会产生空字符串。
const value = ref<string | number>('')
const saleTarget = ref('paused')
const archiveTarget = ref(true)
const contentChanges = ref<Record<string,unknown>>({})
const contentValid = ref(true)
const contentAttributeNames = ref<Record<string,string>>({})
const contentPicturePreviews = ref<string[]>([])
const preview = ref(false), submitKey = ref('')
let timer: ReturnType<typeof setTimeout> | undefined
let sequence = 0
let disposed = false
const price = computed(() => selected.value?.prices.find(p => p.id === scope.value))
const stock = computed(() => selected.value?.stocks.find(s => s.id === scope.value))
const hasActive = computed(() => Boolean(page.value?.jobs.some(j => ['queued', 'running'].includes(j.status))))
const storeSync = computed(() => page.value?.jobs.find(j => j.operation === 'sync' && j.request.source !== 'publish' && ['queued', 'running'].includes(j.status)))
const ingestSync = computed(() => page.value?.jobs.find(j => j.operation === 'sync' && j.request.source === 'publish' && ['queued', 'running'].includes(j.status)))
const activeSync = computed(() => storeSync.value && (!storeSync.value.request.catalog || storeSync.value.request.catalog === catalog.value) ? storeSync.value : undefined)
const otherCatalogSync = computed(() => !!storeSync.value && !activeSync.value)
const canHotRefresh = computed(() => !!page.value?.account_id && (page.value?.summary.total || 0) > 0 && !activeSync.value && !otherCatalogSync.value && !ingestSync.value)
const stale = computed(() => !!page.value?.latest_sync && Date.now() - Date.parse(page.value.latest_sync.updated_at) > 3600000)
const hasDimensionFilters = computed(() => Boolean(query.value.trim() || status.value || market.value || warehouse.value || stockState.value || issueState.value || priceState.value || groupKind.value))
const activeFilterCount = computed(() => [
  query.value.trim(),
  platform.value === 'yandex' && catalog.value === 'archived' ? 'archived' : '',
  warehouse.value,
  market.value,
  status.value,
  stockState.value,
  issueState.value,
  priceState.value,
  groupKind.value,
].filter(Boolean).length)
const activeStatusValue = computed(() => platform.value === 'yandex' ? 'PUBLISHED' : 'active')
const pausedStatusValue = computed(() => platform.value === 'yandex' ? 'DISABLED_BY_PARTNER' : 'paused')
function summaryActive(key: 'total' | 'active' | 'paused' | 'attention') {
  if (key === 'total') return !status.value && !stockState.value && !issueState.value && !priceState.value && !groupKind.value
  if (key === 'active') return status.value === activeStatusValue.value && !issueState.value
  if (key === 'paused') return status.value === pausedStatusValue.value && !issueState.value
  return issueState.value === 'attention' && !status.value
}
function applySummaryShortcut(key: 'total' | 'active' | 'paused' | 'attention') {
  if (key === 'total') {
    status.value = ''; stockState.value = ''; issueState.value = ''; priceState.value = ''; groupKind.value = ''
  } else if (key === 'active') {
    issueState.value = ''
    status.value = status.value === activeStatusValue.value ? '' : activeStatusValue.value
  } else if (key === 'paused') {
    issueState.value = ''
    status.value = status.value === pausedStatusValue.value ? '' : pausedStatusValue.value
  } else {
    status.value = ''
    issueState.value = issueState.value === 'attention' ? '' : 'attention'
  }
  filter()
}
function resetFilters() {
  query.value = ''; status.value = ''; market.value = ''; warehouse.value = ''
  stockState.value = ''; issueState.value = ''; priceState.value = ''; groupKind.value = ''
  if (catalog.value !== 'current') {
    catalog.value = 'current'
    selected.value = null
    expandedGroups.value.clear()
  }
  filter()
}
const saleMarket = computed(() => selected.value?.markets.find(market=>`campaign:${market.id}`===scope.value))
const currentSaleState = computed(() => saleMarket.value ? (saleMarket.value.raw_sub_status?.includes('hidden') ? 'paused' : saleMarket.value.raw_status==='PUBLISHED' ? 'active' : 'unknown') : selected.value?.sale_state)
const saleState = computed(() => currentSaleState.value === 'paused' ? 'active' : 'paused')
const cardNotice = computed(() => selected.value?.raw_sub_status
  .filter(status => ['HAS_CARD_CAN_NOT_UPDATE', 'HAS_CARD_CAN_UPDATE_ERRORS', 'NO_CARD_NEED_CONTENT', 'NO_CARD_MARKET_WILL_CREATE', 'NO_CARD_ERRORS', 'NO_CARD_ADD_TO_CAMPAIGN'].includes(status))
  .map(label).join('、') || '')
const fieldNames: Record<string,string> = {preserved_pause:'保持主动停售',amount:'价格', currency:'币种', quantity:'绝对库存', title:'标题', description:'描述', pictures:'完整图片列表', attributes:'属性', guarantee_period:'保修期限', state:'销售状态', archived:'归档状态'}
const changes = computed<Record<string, unknown>>(() => {
  if (modal.value === 'price') return {amount: String(value.value), currency: price.value?.currency || ''}
  if (modal.value === 'stock') return {quantity: Number(value.value)}
  if (modal.value === 'sale_state') return {state: saleTarget.value}
  if (modal.value === 'archive') return {archived: archiveTarget.value}
  if (modal.value === 'content') return contentChanges.value
  return {}
})
function syncTarget(job: OnlineJob) {
  if (job.request.source === 'publish') {
    return typeof job.request.ingest_count === 'number' && job.request.ingest_count > 0
      ? `新发商品入库（${job.request.ingest_count} 件）`
      : '新发商品入库'
  }
  const name = job.request.catalog === 'archived' ? '归档商品' : job.request.catalog === 'current' ? '普通商品' : '全店商品'
  const isHot = job.request.mode === 'hot' || job.result.data_scope === 'hot'
  const base = isHot ? `${name} · 价格与库存快刷` : name
  return typeof job.request.retry_count === 'number' && job.request.retry_count > 0 ? `${base} · 失败商品（${job.request.retry_count} 件）` : base
}
const syncStatusTone = computed(() => {
  const s = page.value?.latest_sync?.status
  if (!s) return 'muted'
  if (s === 'confirmed') return 'success'
  if (s === 'failed') return 'danger'
  if (s === 'partial') return 'warning'
  return 'info'
})
function showSyncProgressText(job: OnlineJob) {
  return ['queued', 'running'].includes(job.status) || job.result.discovery_complete === false
}
const summaryItems = computed(() => {
  const s = page.value?.summary || { total: 0, active: 0, paused: 0, attention: 0 }
  return [
    { key: 'total', label: '全部 SKU', count: s.total, tone: 'default' },
    { key: 'active', label: '在售', count: s.active, tone: 'success' },
    { key: 'paused', label: '已停售', count: s.paused, tone: 'muted' },
    { key: 'attention', label: '需关注', count: s.attention, tone: s.attention > 0 ? 'warning' : 'muted' },
  ] as const
})
function showSync() {
  if (otherCatalogSync.value) return
  if (activeSync.value) emit('open-task', activeSync.value.id)
  else begin('sync')
}
async function startHotRefresh() {
  if (busy.value || !canHotRefresh.value) return
  busy.value = true; error.value = ''
  try {
    const job = await onlineAction('sync', {platform: platform.value, catalog: platform.value === 'yandex' ? catalog.value : 'current', mode: 'hot', idempotency_key: crypto.randomUUID()})
    noticeTaskId.value = job.id
    notice.value = platform.value === 'yandex'
      ? `${catalog.value === 'archived' ? '归档商品' : '普通商品'}价格与库存快刷已排队，跳过目录图文，数秒内自动更新。`
      : `${platforms[platform.value]} 已有商品快速刷新已排队，跳过全店目录扫描，完成后自动更新。`
    await refresh(true)
  } catch (e) {
    error.value = e instanceof Error ? e.message : '快速刷新提交失败'
    const raw = e && typeof e === 'object' && 'raw' in e ? e.raw : null
    if (raw && typeof raw === 'object' && 'error_code' in raw && raw.error_code === 'ONLINE_CONFLICT'
      && 'conflict_job_id' in raw && typeof raw.conflict_job_id === 'string') conflictJobId.value = raw.conflict_job_id
  } finally { busy.value = false }
}
function syncProgress(job: OnlineJob) {
  const result = job.result
  const processed = (result.completed || 0) + (result.failed || 0)
  if (job.status === 'queued') return '同步已排队，可离开页面'
  if (job.status === 'running' && result.phase === 'hot') return `正在快速刷新价格、库存与销售状态，已处理 ${processed} / ${result.discovered || 0} 件`
  if (job.status === 'running' && result.phase === 'catalog') return `正在读取商品目录，已发现 ${result.discovered || 0} 件`
  if (job.status === 'running' && result.phase === 'basic') return `正在同步商品资料、价格、库存及平台反馈，已处理 ${processed} 件`
  if (job.status === 'running' && result.phase === 'details') return result.discovery_complete
    ? `正在补齐详情，已处理 ${processed} / ${result.discovered || 0} 件`
    : `正在同步商品，已处理 ${processed} 件`
  return `已处理 ${processed} 件${result.discovery_complete === false ? '，目录尚未读取完整' : ''}`
}
function detailState(row: OnlineListing) {
  return row.details_state === 'pending' ? (row.platform === 'yandex' ? '基础资料同步中' : '详情同步中') : row.details_state === 'failed' ? (row.platform === 'yandex' ? '资料读取失败，请重新读取' : '详情同步失败，请到任务队列重试失败项') : ''
}
function cachedDetails(row: OnlineListing) { return !row.sales_checked_at && !!row.basic_synced_at && !!row.synced_at && row.basic_synced_at > row.synced_at }
function label(s: string) { return s === 'archive' ? '归档 / 恢复归档' : s === 'unknown' ? '待核实' : labels[s] || s || '未知' }
function listingStatus(row: OnlineListing) {
  if (row.archived) return 'ARCHIVED'
  if (row.platform==='yandex' && warehouse.value) {
    const campaign = selectedWarehouse.value?.campaign_id
    const market = row.markets.find(item=>item.id===campaign)
    const inventory = row.stocks.find(item=>item.id===warehouse.value)
    if (market?.raw_sub_status?.includes('hidden') || inventory?.sale_state==='paused') return 'paused'
    return market?.raw_status || inventory?.raw_status || 'unknown'
  }
  if (row.sale_state === 'paused') return 'paused'
  return row.platform === 'yandex' && row.raw_status === 'ARCHIVED' ? 'unknown' : row.raw_status
}
function switchCatalog(next: 'current' | 'archived') { catalog.value=next; status.value=''; selected.value=null; expandedGroups.value.clear(); filter() }
function feedbackCounts(row: OnlineListing) {
  const issues = row.platform_issues || []
  return {affected_sku_count: issues.length ? 1 : 0,
    error_count: issues.filter(issue => issue.severity === 'error').length,
    warning_count: issues.filter(issue => issue.severity === 'warning').length}
}
function issueSummary(row: OnlineListing) {
  const counts = feedbackCounts(row)
  return [counts.error_count ? `${counts.error_count} 个平台错误` : '', counts.warning_count ? `${counts.warning_count} 个平台警告` : ''].filter(Boolean).join(' · ')
}
function time(s: string) { return s ? new Date(s).toLocaleString('zh-CN', {hour12:false}) : '尚未同步' }
function badge(s: string) { return ['confirmed','active','PUBLISHED'].includes(s) ? 'badge-success' : ['failed','outcome_unknown','NO_STOCKS'].includes(s) ? 'badge-danger' : 'badge-muted' }
function priceKind(s: string) { return ({net_proceeds:'净收入报价',base_price:'基础价',sale_price:'售价'} as Record<string,string>)[s] || s }
const brokenThumbnails = ref(new Set<string>())
function thumbnailSrc(url?: string) {
  return url && !brokenThumbnails.value.has(url) ? url : ''
}
function markThumbnailBroken(url?: string) {
  if (!url || brokenThumbnails.value.has(url)) return
  brokenThumbnails.value = new Set([...brokenThumbnails.value, url])
}
function salesNotes(scopes: Array<{label: string; cached_at?: string; read_error?: string}>) {
  return scopes.filter(scope => scope.read_error).map(scope =>
    scope.cached_at ? `${scope.label}沿用 ${time(scope.cached_at)} 的值` : `${scope.label}：${scope.read_error}`)
}
function salesSummaryNote(scopes: Array<{label: string; cached_at?: string; read_error?: string}>, kind: 'price' | 'stock') {
  const failed = scopes.filter(scope => scope.read_error)
  if (!failed.length) return ''
  const cached = failed.filter(scope => scope.cached_at)
  const missing = failed.length - cached.length
  const unit = kind === 'price' ? '价' : '库存'
  if (failed.length === 1) {
    return cached.length ? `沿用旧${unit}` : `${kind === 'price' ? '价格' : '库存'}读取异常`
  }
  if (cached.length && missing) return `沿用旧${unit} · ${missing} 项未返回`
  if (cached.length) return `${cached.length} 项沿用旧${unit}`
  return `${missing} 项${kind === 'price' ? '价格' : '库存'}未返回`
}
async function refreshPageSales(result: OnlinePage, ticket: number) {
  const ids = result.sales_due_ids || []
  try {
    for (let offset = 0; offset < ids.length; offset += 100) {
      if (disposed || document.hidden || ticket !== sequence || hasActive.value || busy.value) return
      await refreshOnlineSales(result.account_id, ids.slice(offset, offset + 100))
    }
    if (!disposed && ticket === sequence) await refresh(true, false)
  } catch (e) {
    const raw = e && typeof e === 'object' && 'raw' in e ? e.raw : null
    if (raw && typeof raw === 'object' && 'error_code' in raw && raw.error_code === 'ONLINE_CONFLICT') return
    if (!disposed && ticket === sequence) error.value = e instanceof Error ? e.message : '价格与库存刷新失败'
  }
}
async function refresh(quiet = false, checkSales = true) {
  if (disposed || (quiet && document.hidden)) return
  const ticket = ++sequence
  if (!quiet) loading.value = true
  try {
    const result = await fetchOnlineProducts({
      platform: platform.value,
      q: query.value,
      status: status.value,
      market: market.value,
      page: currentPage.value,
      catalog: catalog.value,
      ...(platform.value === 'yandex' && warehouse.value ? { warehouse_id: warehouse.value } : {}),
      ...(stockState.value ? { stock_state: stockState.value } : {}),
      ...(issueState.value ? { issue_state: issueState.value } : {}),
      ...(priceState.value ? { price_state: priceState.value } : {}),
      ...(groupKind.value ? { group_kind: groupKind.value } : {}),
    })
    if (ticket !== sequence) return
    if (page.value?.account_id !== result.account_id) {
      clearSelection()
      expandedGroups.value.clear()
      focusedProduct.value = null
      selected.value = null
    }
    page.value = result
    if (warehouse.value && !result.warehouses?.some(item=>item.id===warehouse.value)) {
      warehouse.value=''; await refresh(quiet); return
    }
    const visibleIds = new Set(result.items.map(row => row.id))
    checkedListings.value = new Set([...checkedListings.value].filter(id => visibleIds.has(id)))
    checkedGroups.value = new Set([...checkedGroups.value].filter(id=>result.groups.some(group=>group.id===id)))
    const focus = focusedProduct.value
    if (focus && !result.groups.some(group => group.id === focus.group_id
      && (!focus.listing_id || group.item_ids.includes(focus.listing_id)))) focusedProduct.value = null
    error.value = ''
    if (checkSales && platform.value === 'yandex' && result.sales_due_ids?.length && !hasActive.value && !selected.value && !modal.value) {
      void refreshPageSales(result, ticket)
    }
  } catch (e) { if(ticket === sequence) error.value = e instanceof Error ? e.message : '读取失败' }
  finally {
    if(ticket === sequence) loading.value = false
    if (!disposed && ticket === sequence) {
      clearTimeout(timer)
      if (!document.hidden && hasActive.value) timer = setTimeout(() => void refresh(true), 5000)
    }
  }
}
async function detail(row: OnlineListing) {
  if (detailLoading.value && selected.value?.id === row.id) return
  const group = page.value?.groups.find(group => group.item_ids.includes(row.id))
  if (group) focusedProduct.value = {group_id: group.id, listing_id: row.id}
  error.value = ''
  const epoch = viewEpoch
  const ticket = ++detailSequence
  selected.value = row
  detailLoading.value = true
  detailError.value = ''
  try {
    let result = row.platform === 'yandex' ? await refreshOnlineDetail(row.id) : await fetchOnlineDetail(row.id)
    const current = page.value?.items.find(item => item.id === row.id)
    if (current && current.status_checked_at > result.status_checked_at && ticket === detailSequence && !disposed && selected.value?.id === row.id) {
      result = await fetchOnlineDetail(row.id)
    }
    if (disposed || ticket !== detailSequence || epoch !== viewEpoch || selected.value?.id !== row.id || page.value?.account_id !== result.account_id) return
    selected.value = result
    page.value.items = page.value.items.map(item => item.id === result.id ? selected.value! : item)
  } catch(e) {
    if (!disposed && ticket === detailSequence && epoch === viewEpoch && selected.value?.id === row.id)
      detailError.value = e instanceof Error ? e.message : '详情读取失败，已有数据已保留'
  } finally { if (ticket === detailSequence) detailLoading.value = false }
}
function statusRefreshBlocked(row: OnlineListing) {
  return checkingStatus.value.has(row.id) || (detailLoading.value && selected.value?.id === row.id) || !!modal.value || !!page.value?.jobs.some(job =>
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
    const previous = page.value.items.find(item => item.id === result.id)
    if (previous) {
      const before = feedbackCounts(previous), after = feedbackCounts(result)
      // 只调整已刷新 SKU 的贡献，保留被当前筛选隐藏的其他 SKU 的反馈。
      for (const group of page.value.groups.filter(group => group.item_ids.includes(result.id))) {
        if (!group.feedback_summary) continue
        group.feedback_summary.affected_sku_count += after.affected_sku_count - before.affected_sku_count
        group.feedback_summary.error_count += after.error_count - before.error_count
        group.feedback_summary.warning_count += after.warning_count - before.warning_count
      }
    }
    page.value.items = page.value.items.map(item => item.id === result.id ? result : item)
    if (selected.value?.id === result.id) selected.value = result
    await refresh(true)
  } catch(e) {
    if (!disposed && epoch === viewEpoch && page.value?.account_id === row.account_id)
      statusErrors.value[row.id] = e instanceof Error ? e.message : '状态查询失败，原数据已保留'
  } finally { checkingStatus.value.delete(row.id) }
}
function begin(operation: OnlineOperation | 'sync') {
  if (operation === 'sync') syncCatalog.value = catalog.value
  modal.value = operation; preview.value = false; submitKey.value = crypto.randomUUID(); error.value = ''
  contentChanges.value = {}
  contentValid.value = true
  contentAttributeNames.value = {}
  contentPicturePreviews.value = []
  archiveTarget.value = !selected.value?.archived
  scope.value = operation === 'price' ? selected.value?.prices.find(p=>p.writable)?.id || '' : operation === 'stock' ? (selected.value?.stocks.find(s=>s.id===warehouse.value && s.writable) || selected.value?.stocks.find(s=>s.writable))?.id || '' : operation === 'archive' ? 'business' : operation === 'sale_state' && selected.value?.platform==='yandex' && page.value?.warehouses?.length ? (selectedWarehouse.value ? `campaign:${selectedWarehouse.value.campaign_id}` : '') : 'global'
  saleTarget.value = saleState.value
  resetValue()
}
function stockSummary(row: OnlineListing) {
  const scopes = warehouse.value && row.platform==='yandex' ? row.stocks.filter(item=>item.id===warehouse.value) : row.stocks
  const known = scopes.filter(item=>item.quantity!==null)
  return {quantity: known.length ? known.reduce((total,item)=>total+item.quantity!,0) : null,
    partial: known.length>0 && known.length<scopes.length}
}
function resetValue() { value.value = modal.value === 'price' ? price.value?.amount || '' : String(stock.value?.quantity ?? '') }
function makePreview() {
  try {
    if (modal.value === 'content' && !contentValid.value) throw new Error('请先完整填写已勾选的内容')
    if (modal.value==='sale_state' && selected.value?.platform==='yandex' && page.value?.warehouses?.length && !scope.value) throw new Error('请先选择要操作的仓库店铺')
    if(!Object.keys(changes.value).length) throw new Error('请至少选择一个变更字段')
    if (modal.value === 'stock' && (!String(value.value).trim() || !Number.isInteger(Number(value.value)) || Number(value.value)<0)) throw new Error('库存必须是非负整数')
    if (modal.value === 'price' && !(Number(value.value)>0)) throw new Error('请输入有效价格')
    preview.value = true; error.value = ''
  } catch(e) { error.value = e instanceof Error ? e.message : String(e) }
}
function beforeValue(field: string) {
  if(field==='archived') return !!selected.value?.archived
  if(field==='amount') return price.value?.amount
  if(field==='currency') return price.value?.currency
  if(field==='quantity') return stock.value?.quantity
  if(field==='state') return currentSaleState.value
  return selected.value?.content[field as keyof OnlineListing['content']]
}
function displayValue(field: string, data: unknown): string {
  if(field==='guarantee_period') return formatYandexWarranty(data)
  if(field==='archived') return data ? '已归档' : '普通商品'
  if(field==='state') return label(String(data || ''))
  if(field==='pictures' && Array.isArray(data)) return `${data.length} 张图片（按显示顺序提交）`
  if(field==='attributes' && Array.isArray(data)) {
    const ids = new Set((changes.value.attributes as Array<{id:unknown}> || []).map(a=>String(a.id)))
    return data.filter(a=>ids.has(String(a.id))).map(a=>{
      const name=contentAttributeNames.value[String(a.id)] || selected.value?.content.attributes?.find(row=>String(row.id)===String(a.id))?.name || a.parameterName || a.id
      return `${name}：${a.value_name ?? a.value ?? a.values?.map((v:{name?:string})=>v.name).join('、') ?? '未知'}`
    }).join('；') || '未填写'
  }
  return data == null ? '未提供' : String(data)
}
async function submit() {
  busy.value = true; error.value = ''
  try {
    const body = modal.value === 'sync' ? {platform: platform.value, catalog: syncCatalog.value, idempotency_key: submitKey.value} : {listing_id: selected.value!.id, version: selected.value!.version, operation: modal.value, scope_id: scope.value, changes: changes.value, idempotency_key: submitKey.value}
    const job = await onlineAction(modal.value === 'sync' ? 'sync' : 'change', body)
    modal.value = ''; selected.value = null; noticeTaskId.value = job.id
    notice.value = job.operation === 'sync' ? `${syncCatalogLabel.value}同步已排队，可离开页面。${platform.value === 'yandex' ? '价格、库存和平台反馈会随同步更新。' : '商品目录与详情进度会持续更新。'}` : '任务已提交，可离开页面。处理结果可在任务队列查看。'
    await refresh(true)
  } catch(e) {
    error.value = e instanceof Error ? e.message : '提交失败'
    const raw = e && typeof e === 'object' && 'raw' in e ? e.raw : null
    if (raw && typeof raw === 'object' && 'error_code' in raw && raw.error_code === 'ONLINE_CONFLICT'
      && 'conflict_job_id' in raw && typeof raw.conflict_job_id === 'string') conflictJobId.value = raw.conflict_job_id
  } finally {busy.value=false}
}
function filter() {clearSelection(); focusedProduct.value=null; currentPage.value=1; void refresh()}
watch(currentPage, clearSelection, {flush:'sync'})
watch(currentPage, () => { focusedProduct.value = null }, {flush:'sync'})
watch(platform, () => { viewEpoch++; warehouse.value=''; catalog.value='current'; stockState.value=''; issueState.value=''; priceState.value=''; groupKind.value=''; focusedProduct.value=null; statusErrors.value={}; brokenThumbnails.value=new Set(); modal.value=''; notice.value=''; page.value=null; selected.value=null; noticeTaskId.value=''; expandedGroups.value.clear(); status.value=market.value=''; filter() })
function visibilityChanged() {
  clearTimeout(timer)
  if (!document.hidden) void refresh(true)
}
onMounted(() => {
  document.addEventListener('visibilitychange', visibilityChanged)
  void refresh()
})
onBeforeUnmount(() => {disposed=true; sequence++; clearTimeout(timer); document.removeEventListener('visibilitychange', visibilityChanged)})
</script>

<template>
  <section class="online-products space-y-4">
    <PageHeader title="在线商品">
      <template #actions>
        <div class="flex flex-wrap items-center justify-end gap-2">
          <button type="button" class="btn btn-outline !min-h-9 !rounded-xl !px-3 !py-1.5 text-sm" @click="emit('open-task')"><PhListChecks :size="15" aria-hidden="true" />任务队列</button>
          <button
            v-if="canHotRefresh"
            type="button"
            data-testid="hot-refresh-btn"
            class="btn btn-primary !min-h-9 !rounded-xl !px-3.5 !py-1.5 text-sm"
            :disabled="busy || loading"
            :title="platform === 'yandex' ? '仅刷新当前目录已有商品的价格、可售库存与销售状态（跳过目录图文，数秒完成）' : '跳过全店目录翻页，直接并发刷新本地已有商品的最新价格、库存与销售状态'"
            @click="startHotRefresh"
          ><PhLightning :size="15" weight="fill" aria-hidden="true" />刷新价格与库存</button>
          <button
            type="button"
            :class="canHotRefresh ? 'btn btn-outline !min-h-9 !rounded-xl !px-3 !py-1.5 text-sm' : 'btn btn-primary !min-h-9 !rounded-xl !px-3.5 !py-1.5 text-sm'"
            :disabled="busy || loading || !page?.account_id || otherCatalogSync"
            :title="canHotRefresh ? (platform === 'yandex' ? '全量同步目录、图文、卡片反馈并清理已删除商品' : '全量发现店铺新增/已删除商品并同步完整信息') : ''"
            @click="showSync"
          ><PhArrowsClockwise v-if="activeSync" :size="15" class="animate-spin" aria-hidden="true" />{{ activeSync ? '查看同步进度' : otherCatalogSync ? '其他目录同步中' : '↻ 同步商品' }}</button>
        </div>
      </template>
    </PageHeader>

    <div class="rounded-2xl border border-accent-200/90 bg-white p-4 shadow-sm dark:border-dark-700 dark:bg-dark-800/70">
      <!-- 顶栏：平台切换 + 紧凑指标概览（点击可快捷筛选） -->
      <div class="flex flex-wrap items-center justify-between gap-3 border-b border-accent-200/80 pb-3 dark:border-dark-700">
        <div class="inline-flex items-center gap-1 rounded-xl bg-accent-100/90 p-1 dark:bg-dark-900" role="tablist" aria-label="平台">
          <button v-for="(name,key) in platforms" :key="key" type="button" role="tab" :disabled="busy" :aria-selected="platform===key" class="seg-tab" @click="platform=key">{{ name }}</button>
        </div>

        <div class="flex flex-wrap items-center gap-1.5 text-xs" role="group" aria-label="商品概览">
          <button
            v-for="metric in summaryItems"
            :key="metric.key"
            type="button"
            :data-testid="`summary-${metric.key}`"
            class="summary-chip"
            :data-active="summaryActive(metric.key) || undefined"
            :disabled="loading || (metric.key !== 'total' && metric.count === 0 && !summaryActive(metric.key))"
            @click="applySummaryShortcut(metric.key)"
          >
            <span class="text-accent-500 dark:text-accent-400">{{ metric.label }}</span>
            <span
              class="text-sm font-bold tabular-nums"
              :class="metric.tone === 'success' ? 'text-primary-700 dark:text-primary-400' : metric.tone === 'warning' ? 'text-amber-600 dark:text-amber-400' : metric.tone === 'muted' ? 'text-accent-500 dark:text-accent-400' : 'text-accent-950 dark:text-white'"
            >
              {{ metric.count }}
            </span>
          </button>
        </div>
      </div>

      <!-- 通知与错误提示（紧凑单行 + 暗色适配） -->
      <div
        v-if="error"
        role="alert"
        class="mt-3 flex items-center justify-between gap-2 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-xs font-medium text-rose-700 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-200"
      >
        <div class="flex min-w-0 items-center gap-2">
          <PhWarningCircle :size="15" weight="fill" class="shrink-0 text-rose-500 dark:text-rose-400" aria-hidden="true" />
          <span class="min-w-0 break-words">{{ error }}</span>
        </div>
        <button type="button" class="shrink-0 font-semibold underline hover:opacity-80" @click="refresh()">重新读取</button>
      </div>

      <div
        v-if="notice && !error"
        role="status"
        class="mt-3 flex items-center justify-between gap-2 rounded-xl border border-primary-200/80 bg-primary-50/80 px-3 py-2 text-xs font-medium text-primary-800 dark:border-primary-500/25 dark:bg-primary-500/10 dark:text-primary-200"
      >
        <div class="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
          <PhCheckCircle :size="15" weight="fill" class="shrink-0 text-primary-600 dark:text-primary-400" aria-hidden="true" />
          <span>{{ notice }}</span>
          <button v-if="noticeTaskId" type="button" class="font-semibold underline hover:opacity-80" @click="emit('open-task', noticeTaskId)">查看任务</button>
        </div>
        <button type="button" aria-label="关闭通知" class="shrink-0 rounded-md p-0.5 opacity-70 hover:opacity-100" @click="notice=''; noticeTaskId=''">
          <PhX :size="13" aria-hidden="true" />
        </button>
      </div>

      <!-- 同步状态条（合并同步进度、计数、时间与过期提醒，消除重复卡片） -->
      <div
        v-if="page?.latest_sync"
        data-testid="online-sync-bar"
        class="mt-3 flex flex-wrap items-center justify-between gap-x-4 gap-y-1.5 rounded-xl border border-accent-200/80 bg-accent-50/70 px-3 py-2 text-xs dark:border-dark-700 dark:bg-dark-900/60"
      >
        <div class="flex min-w-0 flex-wrap items-center gap-x-2.5 gap-y-1">
          <span class="sync-pill" :data-tone="syncStatusTone">
            <span class="sync-dot" :class="{ 'animate-pulse': ['queued', 'running'].includes(page.latest_sync.status) }" aria-hidden="true" />
            最近同步 · {{ syncTarget(page.latest_sync) }} · {{ page.latest_sync.status === 'confirmed' ? '同步完成' : label(page.latest_sync.status) }}
          </span>
          <span v-if="showSyncProgressText(page.latest_sync)" role="status" class="font-medium text-accent-700 dark:text-accent-200">{{ syncProgress(page.latest_sync) }}</span>
          <span class="tabular-nums text-accent-500 dark:text-accent-400">
            成功 {{ page.latest_sync.result.completed || 0 }}
            · 新增 {{ page.latest_sync.result.created || 0 }}
            · 更新 {{ page.latest_sync.result.updated || 0 }}
            <template v-if="page.latest_sync.result.removed"> · 已清理 {{ page.latest_sync.result.removed }}</template>
            <template v-if="page.latest_sync.result.moved"> · 调整目录 {{ page.latest_sync.result.moved }}</template>
            <template v-if="page.latest_sync.result.failed"> · <span class="font-semibold text-rose-600 dark:text-rose-400">失败 {{ page.latest_sync.result.failed }}</span></template>
            <template v-else> · 失败 0</template>
          </span>
          <span v-if="page.latest_sync.result.error" class="font-medium text-rose-600 dark:text-rose-300">{{ page.latest_sync.result.error }}</span>
        </div>
        <div class="flex shrink-0 flex-wrap items-center gap-3">
          <span v-if="stale" class="inline-flex items-center gap-1 rounded-md bg-amber-100/80 px-2 py-0.5 text-[11px] font-medium text-amber-800 dark:bg-amber-500/15 dark:text-amber-300" title="价格、库存和内容快照已超过一小时，可同步更新；商品状态可单独刷新。">
            <PhClockCountdown :size="12" aria-hidden="true" />快照已超 1 小时
          </span>
          <span class="tabular-nums text-[11px] text-accent-500 dark:text-accent-400">{{ time(page.latest_sync.updated_at) }}</span>
          <button type="button" class="font-semibold text-primary-700 hover:underline dark:text-primary-400" @click="emit('open-task', page.latest_sync.id)">查看结果 →</button>
        </div>
      </div>

      <!-- 新发商品自动定向入库进度条 -->
      <div
        v-if="ingestSync"
        data-testid="online-ingest-bar"
        role="status"
        class="mt-3 flex flex-wrap items-center justify-between gap-2 rounded-xl border border-primary-200/80 bg-primary-50/70 px-3 py-2 text-xs text-primary-900 dark:border-primary-500/25 dark:bg-primary-500/10 dark:text-primary-200"
      >
        <div class="flex min-w-0 flex-wrap items-center gap-2">
          <PhArrowsClockwise :size="14" class="shrink-0 animate-spin text-primary-600 dark:text-primary-400" aria-hidden="true" />
          <span class="font-medium">{{ syncTarget(ingestSync) }}执行中，完成后自动出现在下方列表。</span>
        </div>
        <button type="button" class="shrink-0 font-semibold underline hover:opacity-80" @click="emit('open-task', ingestSync.id)">查看任务 →</button>
      </div>

      <!-- 商品表格（顶部集成搜索、多维筛选与批量操作） -->
      <div class="mt-3 rounded-xl border border-accent-200/90 bg-white dark:border-dark-700 dark:bg-dark-900/30">
        <form aria-label="商品搜索与筛选" class="space-y-2.5 rounded-t-xl border-b border-accent-200/80 bg-accent-50/55 px-3 py-2.5 dark:border-dark-700 dark:bg-dark-900/55" @submit.prevent="filter">
          <div class="flex flex-wrap items-center gap-2">
            <div class="relative min-w-[220px] flex-1 sm:max-w-sm">
              <PhMagnifyingGlass :size="14" aria-hidden="true" class="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-accent-400" />
              <input v-model="query" class="input h-8 !rounded-xl !py-0 !pl-8 !pr-7 text-xs" placeholder="搜索标题、卖家 SKU 或平台商品 ID" aria-label="搜索商品" />
              <button v-if="query" type="button" aria-label="清空搜索" class="absolute right-1.5 top-1/2 -translate-y-1/2 rounded-md p-0.5 text-accent-400 hover:bg-accent-100 hover:text-accent-700 dark:hover:bg-dark-800 dark:hover:text-accent-200" @click="query=''; filter()">
                <PhX :size="12" aria-hidden="true" />
              </button>
            </div>

            <button class="inline-flex h-8 items-center rounded-xl border border-accent-300 bg-white px-3 text-xs font-semibold text-accent-700 transition hover:bg-accent-50 hover:text-accent-950 dark:border-dark-600 dark:bg-dark-900 dark:text-accent-200 dark:hover:bg-dark-800" type="submit">搜索</button>

            <button
              v-if="activeFilterCount > 0"
              type="button"
              data-testid="reset-filters"
              class="inline-flex h-8 items-center gap-1 rounded-xl px-2.5 text-xs font-medium text-accent-600 transition hover:bg-accent-100 hover:text-accent-950 dark:text-accent-300 dark:hover:bg-white/5 dark:hover:text-white"
              @click="resetFilters"
            >清空筛选（{{ activeFilterCount }}）</button>

            <div v-if="platform==='yandex'" ref="actionMenu" class="relative ml-auto flex items-center gap-2" @keydown.esc.stop="actionMenuOpen=false">
              <span v-if="checkedListings.size" class="inline-flex items-center gap-1.5 rounded-lg bg-primary-50 px-2.5 py-1 text-xs font-medium text-primary-800 tabular-nums dark:bg-primary-500/15 dark:text-primary-200">
                已选 {{ checkedListings.size }} 个 SKU
                <button type="button" class="text-[11px] underline opacity-80 hover:opacity-100" @click="clearSelection">清空</button>
              </span>
              <button type="button" class="inline-flex h-8 items-center gap-1.5 rounded-xl border border-accent-300 bg-white px-3 text-xs font-semibold text-accent-700 transition hover:bg-accent-50 hover:text-accent-950 dark:border-dark-600 dark:bg-dark-900 dark:text-accent-200 dark:hover:bg-dark-800" aria-haspopup="true" :aria-expanded="actionMenuOpen" aria-controls="online-product-actions" data-testid="online-actions" @click="actionMenuOpen=!actionMenuOpen">
                <span>操作</span>
                <PhCaretDown :size="12" class="transition-transform" :class="actionMenuOpen ? 'rotate-180' : ''" aria-hidden="true" />
              </button>
              <div v-if="actionMenuOpen" id="online-product-actions" class="absolute right-0 top-full z-20 mt-1.5 w-52 rounded-xl border border-accent-200 bg-white p-1.5 shadow-lg dark:border-dark-600 dark:bg-dark-900">
                <button v-if="catalog==='current'" type="button" class="online-action" :disabled="!canSaleSelection || loading || busy" @click="beginSelection('sale_state','paused')">停售所选商品</button>
                <button v-if="catalog==='current'" type="button" class="online-action" :disabled="!canSaleSelection || loading || busy" @click="beginSelection('sale_state','active')">恢复所选商品销售</button>
                <button type="button" class="online-action" :disabled="!canArchiveSelection || loading || busy" @click="beginSelection('archive',catalog!=='archived')">{{ catalog==='archived' ? '恢复所选归档' : '归档所选商品' }}</button>
                <button type="button" class="online-action" :disabled="!checkedListings.size || loading || busy" data-testid="online-export-ozon" @click="beginOzonExport">导出到 Ozon<span v-if="checkedListings.size" class="mt-0.5 block text-[11px] font-normal text-accent-500 dark:text-accent-400">当前筛选已选 {{ checkedListings.size }} 个 SKU</span></button>
              </div>
            </div>
          </div>

          <div class="flex flex-wrap items-center gap-2" data-testid="online-filter-row">
            <label v-if="platform==='yandex'" class="filter-chip" :data-active="catalog==='archived' || undefined">
              <span class="filter-label">目录</span>
              <select :value="catalog" class="filter-select" aria-label="商品目录" data-testid="filter-catalog" :disabled="busy" @change="switchCatalog(($event.target as HTMLSelectElement).value as 'current' | 'archived')">
                <option value="current">普通商品</option>
                <option value="archived">归档商品</option>
              </select>
            </label>

            <label v-if="platform==='yandex'" class="filter-chip" :data-active="!!warehouse || undefined">
              <span class="filter-label">仓库</span>
              <select v-model="warehouse" class="filter-select" aria-label="仓库" @change="clearSelection(); filter()">
                <option value="">全部仓库</option>
                <option v-for="item in page?.warehouses" :key="item.id" :value="item.id">{{ item.name }}</option>
              </select>
            </label>

            <label v-if="platform!=='yandex'" class="filter-chip" :data-active="!!market || undefined">
              <span class="filter-label">市场</span>
              <select v-model="market" class="filter-select" aria-label="销售市场" @change="filter">
                <option value="">全部市场</option>
                <option v-for="s in page?.markets" :key="s">{{ s }}</option>
              </select>
            </label>

            <label class="filter-chip" :data-active="!!status || undefined">
              <span class="filter-label">状态</span>
              <select v-model="status" class="filter-select" aria-label="销售状态" @change="filter">
                <option value="">全部状态</option>
                <option v-for="s in page?.statuses" :key="s" :value="s">{{ label(s) }}</option>
              </select>
            </label>

            <label class="filter-chip" :data-active="!!stockState || undefined">
              <span class="filter-label">库存</span>
              <select v-model="stockState" class="filter-select" aria-label="库存状态" data-testid="filter-stock" @change="filter">
                <option value="">全部库存</option>
                <option value="in_stock">有库存</option>
                <option value="out_of_stock">零库存</option>
                <option value="unknown">库存未知</option>
              </select>
            </label>

            <label class="filter-chip" :data-active="!!issueState || undefined">
              <span class="filter-label">反馈</span>
              <select v-model="issueState" class="filter-select" aria-label="平台反馈" data-testid="filter-issue" @change="filter">
                <option value="">全部反馈</option>
                <option value="attention">需关注</option>
                <option value="error">有平台错误</option>
                <option value="warning">有平台警告</option>
                <option value="clean">无异常反馈</option>
              </select>
            </label>

            <label class="filter-chip" :data-active="!!priceState || undefined">
              <span class="filter-label">价格</span>
              <select v-model="priceState" class="filter-select" aria-label="价格状态" data-testid="filter-price" @change="filter">
                <option value="">全部价格</option>
                <option value="priced">已有报价</option>
                <option value="missing">暂无报价</option>
                <option value="read_error">报价读取异常</option>
              </select>
            </label>

            <label class="filter-chip" :data-active="!!groupKind || undefined">
              <span class="filter-label">形态</span>
              <select v-model="groupKind" class="filter-select" aria-label="商品形态" data-testid="filter-kind" @change="filter">
                <option value="">全部形态</option>
                <option value="group">组合商品</option>
                <option value="single">单品</option>
              </select>
            </label>
          </div>
        </form>

        <div class="overflow-x-auto" :aria-busy="loading">
          <table class="w-full min-w-[900px] text-left text-xs">
          <thead class="border-b border-accent-200/80 bg-accent-50/90 text-xs font-semibold text-accent-600 dark:border-dark-700 dark:bg-dark-900/80 dark:text-accent-300">
            <tr class="whitespace-nowrap">
              <th v-if="platform==='yandex'" class="w-10 py-2.5 text-center" aria-label="选择 SKU" />
              <th class="px-3 py-2.5">商品</th>
              <th class="w-[136px] px-3 py-2.5">价格</th>
              <th class="w-[132px] px-3 py-2.5">库存</th>
              <th class="w-[210px] px-3 py-2.5">销售状态</th>
              <th class="w-[156px] px-3 py-2.5">最近同步</th>
              <th class="w-[148px] px-3 py-2.5 text-right">操作</th>
            </tr>
          </thead>
          <tbody v-for="group in listingGroups" :key="group.id" class="border-t border-accent-200/70 first:border-t-0 dark:border-dark-700/70">
            <tr v-if="group.kind === 'group'" class="online-group transition-colors hover:bg-primary-50/80 dark:hover:bg-primary-500/10" data-testid="online-group" @click="focusedProduct={group_id:group.id}" @focusin="focusedProduct={group_id:group.id}">
              <td v-if="platform==='yandex'" class="py-2.5 text-center">
                <input type="checkbox" class="size-4 cursor-pointer align-middle accent-primary-600" :checked="groupChecked(group.item_ids)" :indeterminate.prop="groupPartial(group.item_ids)" :disabled="loading || busy" :aria-label="`选择商品：${group.title}`" data-testid="select-online-group" @click.stop @change="toggleListings(group.item_ids, ($event.target as HTMLInputElement).checked, group.id)" />
              </td>
              <td class="px-3 py-2.5">
                <button type="button" class="group flex max-w-md items-center gap-2.5 rounded-lg text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 focus-visible:ring-offset-2" data-testid="toggle-group" :aria-expanded="expandedGroups.has(group.id)" :aria-label="`${expandedGroups.has(group.id) ? '收起' : '展开'}组合商品：${group.title}`" @click="toggleGroup(group.id)">
                  <span aria-hidden="true" class="flex size-7 shrink-0 items-center justify-center rounded-lg border border-primary-200 bg-white text-primary-700 transition-colors group-hover:border-primary-400 group-hover:bg-primary-50 dark:border-primary-500/35 dark:bg-dark-800 dark:text-primary-300 dark:group-hover:bg-primary-500/20">
                    <PhCaretRight :size="14" weight="bold" class="transition-transform motion-reduce:transition-none" :class="{'rotate-90': expandedGroups.has(group.id)}" />
                  </span>
                  <img v-if="thumbnailSrc(group.rows[0]?.thumbnail)" :src="thumbnailSrc(group.rows[0]?.thumbnail)" referrerpolicy="no-referrer" alt="" class="size-10 shrink-0 rounded-lg border border-accent-200/70 object-cover dark:border-dark-600" @error="markThumbnailBroken(group.rows[0]?.thumbnail)" />
                  <span v-else class="flex size-10 shrink-0 items-center justify-center rounded-lg border border-accent-200/70 bg-accent-100/80 text-accent-400 dark:border-dark-700 dark:bg-dark-800 dark:text-dark-400" aria-hidden="true"><PhImage :size="18" /></span>
                  <span class="min-w-0">
                    <span class="line-clamp-2 text-[13px] font-semibold text-accent-950 dark:text-white">{{ group.title }}</span>
                    <span class="mt-0.5 inline-flex items-center gap-1.5 text-[11px] font-medium text-primary-700 dark:text-primary-400">组合商品 · {{ group.total_count }} 个 SKU</span>
                  </span>
                </button>
              </td>
              <template v-if="group.sales">
                <td class="px-3 py-2.5 tabular-nums text-accent-900 dark:text-accent-100">
                  <p v-for="groupPrice in group.sales.prices" :key="groupPrice" class="whitespace-nowrap font-medium">{{ groupPrice }}</p>
                  <span v-if="!group.sales.prices.length" class="text-accent-400">—</span>
                  <p v-else-if="group.sales.missingPrice" class="mt-0.5 text-[11px] text-accent-500 dark:text-accent-400">部分 SKU 价格未知</p>
                </td>
                <td class="px-3 py-2.5 tabular-nums text-accent-900 dark:text-accent-100">
                  <p class="font-medium">{{ group.sales.quantity ?? '未知' }}</p>
                  <p v-if="group.sales.partial" class="mt-0.5 text-[11px] text-accent-500 dark:text-accent-400">仅合计已知库存</p>
                </td>
              </template>
              <td :colspan="group.sales ? 3 : 5" class="px-3 py-2.5">
                <div class="flex flex-wrap items-center gap-x-2.5 gap-y-1">
                  <span v-if="group.rows.length < group.total_count" class="font-medium text-accent-800 dark:text-accent-200">当前筛选匹配 {{ group.rows.length }} / {{ group.total_count }} 个 SKU</span>
                  <span v-if="group.sales?.cached" class="text-[11px] text-accent-500 dark:text-accent-400">含上次读取的价格或库存</span>
                  <span v-if="!group.sales && !group.feedback_summary?.affected_sku_count && group.rows.length >= group.total_count" class="text-[11px] text-accent-500 dark:text-accent-400">点击左侧展开查看各 SKU</span>
                  <div v-if="group.feedback_summary?.affected_sku_count" class="flex flex-wrap items-center gap-1.5 text-xs" data-testid="group-feedback">
                    <span class="text-accent-600 dark:text-accent-300">整个组合：{{ group.feedback_summary.affected_sku_count }} 个 SKU 有平台反馈</span>
                    <span v-if="group.feedback_summary.error_count" class="rounded-md bg-rose-50 px-1.5 py-0.5 text-[11px] font-medium text-rose-700 dark:bg-rose-500/15 dark:text-rose-300">{{ group.feedback_summary.error_count }} 条平台错误</span>
                    <span v-if="group.feedback_summary.warning_count" class="rounded-md bg-amber-50 px-1.5 py-0.5 text-[11px] font-medium text-amber-800 dark:bg-amber-500/15 dark:text-amber-300">{{ group.feedback_summary.warning_count }} 条平台警告</span>
                  </div>
                </div>
              </td>
            </tr>
            <template v-if="group.kind === 'single' || expandedGroups.has(group.id)">
              <tr v-for="row in group.rows" :key="row.id" data-testid="online-listing" class="border-t border-accent-200/60 transition-colors hover:bg-accent-50/70 dark:border-dark-700/60 dark:hover:bg-white/[0.03]" :class="{'online-child': group.kind === 'group'}" @click="focusedProduct={group_id:group.id,listing_id:row.id}" @focusin="focusedProduct={group_id:group.id,listing_id:row.id}">
                <td v-if="platform==='yandex'" class="py-2.5 text-center">
                  <input type="checkbox" class="size-4 cursor-pointer align-middle accent-primary-600" :checked="checkedListings.has(row.id)" :disabled="loading" :aria-label="`选择 SKU：${row.seller_sku || row.remote_id}`" data-testid="select-online-listing" @click.stop @change="toggleListings([row.id], ($event.target as HTMLInputElement).checked)" />
                </td>
                <td class="px-3 py-2.5">
                  <div class="flex max-w-md items-center gap-2.5" :class="{'ml-5 border-l-2 border-primary-200 pl-3 dark:border-primary-500/30': group.kind === 'group'}">
                    <img v-if="thumbnailSrc(row.thumbnail)" referrerpolicy="no-referrer" :src="thumbnailSrc(row.thumbnail)" alt="" class="size-10 shrink-0 rounded-lg border border-accent-200/70 object-cover dark:border-dark-600" @error="markThumbnailBroken(row.thumbnail)" />
                    <span v-else data-testid="thumbnail-placeholder" class="flex size-10 shrink-0 items-center justify-center rounded-lg border border-accent-200/70 bg-accent-100/80 text-accent-400 dark:border-dark-700 dark:bg-dark-800 dark:text-dark-400" aria-hidden="true"><PhImage :size="18" /></span>
                    <div class="min-w-0">
                      <p class="line-clamp-2 text-[13px] font-medium text-accent-950 dark:text-white">{{ row.title || row.remote_id }}</p>
                      <p class="mt-0.5 break-all font-mono text-[11px] text-accent-500 dark:text-accent-400">{{ row.seller_sku || row.remote_id }}</p>
                    </div>
                  </div>
                </td>
                <td class="px-3 py-2.5 tabular-nums">
                  <OnlinePriceCell :row="row" :shop-names="shopNames" :store-name="page?.store_name" />
                  <p v-if="salesSummaryNote(row.prices, 'price')" data-testid="price-sales-note" :title="salesNotes(row.prices).join('\n')" class="mt-0.5 text-[11px] text-amber-700 dark:text-amber-300">{{ salesSummaryNote(row.prices, 'price') }}</p>
                </td>
                <td v-if="row.platform==='yandex'" data-testid="stock-summary" class="px-3 py-2.5 tabular-nums">
                  <p class="font-medium text-accent-900 dark:text-accent-100">{{ stockSummary(row).quantity ?? '未知' }}</p>
                  <p v-if="stockSummary(row).partial" class="mt-0.5 text-[11px] text-accent-500 dark:text-accent-400">部分仓库库存未知</p>
                  <p v-if="salesSummaryNote(row.stocks, 'stock')" data-testid="stock-sales-note" :title="salesNotes(row.stocks).join('\n')" class="mt-0.5 text-[11px] text-amber-700 dark:text-amber-300">{{ salesSummaryNote(row.stocks, 'stock') }}</p>
                </td>
                <td v-else class="px-3 py-2.5 tabular-nums">
                  <div v-for="inventory in row.stocks" :key="inventory.id" class="leading-snug">
                    <span class="font-medium text-accent-900 dark:text-accent-100">{{ inventory.quantity ?? '未知' }}</span>
                    <span class="ml-1.5 text-[11px] text-accent-500 dark:text-accent-400">{{ inventory.label }}</span>
                  </div>
                  <p v-if="!row.stocks.length" class="text-[11px] text-accent-400">尚未取得库存范围</p>
                </td>
                <td class="px-3 py-2.5">
                  <span :class="badge(listingStatus(row))">{{ label(listingStatus(row)) }}</span>
                  <p v-if="detailState(row)" class="mt-1 text-[11px] text-amber-700 dark:text-amber-300">{{ detailState(row) }}</p>
                  <p v-else-if="row.errors.length" class="mt-1 text-[11px] text-amber-700 dark:text-amber-300">读取不完整</p>
                  <p v-if="issueSummary(row)" class="mt-1 text-[11px] text-amber-700 dark:text-amber-300">{{ issueSummary(row) }}</p>
                </td>
                <td class="whitespace-nowrap px-3 py-2.5 text-[11px] tabular-nums text-accent-500 dark:text-accent-400">
                  <p>{{ row.basic_synced_at ? '基础同步：' : '' }}{{ time(row.basic_synced_at || row.synced_at) }}</p>
                  <p v-if="row.sales_checked_at && row.sales_checked_at !== row.basic_synced_at" class="mt-0.5">价格与库存查询：{{ time(row.sales_checked_at) }}</p>
                  <p v-if="cachedDetails(row)" class="mt-0.5 text-amber-700 dark:text-amber-300">价格与库存为缓存 · {{ time(row.synced_at) }}</p>
                  <p v-if="row.status_checked_at" class="mt-0.5">状态查询：{{ time(row.status_checked_at) }}</p>
                </td>
                <td class="px-3 py-2.5 text-right">
                  <div class="flex flex-col items-end gap-1.5">
                    <div class="flex flex-wrap items-center justify-end gap-2">
                      <button type="button" class="whitespace-nowrap font-semibold text-primary-700 hover:underline dark:text-primary-400" @click="detail(row)">管理 →</button>
                      <button type="button" data-testid="refresh-status" class="whitespace-nowrap text-accent-600 hover:text-primary-700 disabled:opacity-50 dark:text-accent-300 dark:hover:text-primary-300" :disabled="statusRefreshBlocked(row)" @click="refreshStatus(row)">{{ checkingStatus.has(row.id) ? '查询中…' : '刷新状态' }}</button>
                    </div>
                    <button v-if="row.seller_sku" type="button" data-testid="online-manage-sku-source" class="whitespace-nowrap text-[11px] text-accent-500 hover:text-primary-700 dark:text-accent-400 dark:hover:text-primary-300" @click.stop="skuSourceTarget = row">{{ listingSkuBindings(row) ? (listingSkuBindings(row)!.length ? `1688 货源（${listingSkuBindings(row)!.length}）` : '未绑 1688 货源') : '1688 货源' }}</button>
                    <p v-if="statusErrors[row.id]" role="alert" class="max-w-52 text-left text-[11px] text-rose-700 dark:text-rose-300">{{ statusErrors[row.id] }}</p>
                    <OnlineBuyerLinks :links="row.buyer_links" />
                  </div>
                </td>
              </tr>
            </template>
          </tbody>
          </table>
        </div>

        <div v-if="!page?.items.length" class="p-12 text-center">
          <p class="text-base font-semibold text-accent-900 dark:text-white">{{ loading?'正在读取在线商品…':page?.state==='authorization_required'?'请先连接并验证店铺':page?.state==='never_synced'?'还没有同步店铺商品':hasDimensionFilters?'当前筛选无结果':catalog==='archived'?'暂无符合条件的归档商品':page?.latest_sync?.status==='confirmed' && page.summary.total===0?'店铺同步完成，暂无普通商品':'暂未取得商品，请查看同步结果' }}</p>
          <p class="mt-2 text-xs text-accent-500 dark:text-accent-400">同步会读取店铺已有刊登，包括未通过本 ERP 发布的商品。</p>
          <button v-if="hasDimensionFilters" type="button" class="mt-3 text-xs font-semibold text-primary-700 hover:underline dark:text-primary-400" @click="resetFilters">清空筛选条件</button>
        </div>

        <footer class="flex flex-wrap items-center justify-between gap-2 rounded-b-xl border-t border-accent-200/80 px-3 py-2.5 text-xs dark:border-dark-700">
          <div class="flex flex-wrap items-center gap-x-3 gap-y-1 text-accent-500 dark:text-accent-400">
            <span class="tabular-nums">共 {{ page?.total || 0 }} 个商品节点 · {{ page?.listing_total || 0 }} 条刊登 / SKU，第 {{ currentPage }} 页</span>
            <span class="hidden text-accent-300 dark:text-dark-600 sm:inline">|</span>
            <span>数据来自最近一次平台同步；修改提交后，以平台回读结果确认生效。</span>
          </div>
          <div class="flex items-center gap-2">
            <button type="button" class="inline-flex h-8 items-center rounded-xl border border-accent-300 bg-white px-3 text-xs font-semibold text-accent-700 transition hover:bg-accent-50 disabled:cursor-not-allowed disabled:opacity-45 dark:border-dark-600 dark:bg-dark-900 dark:text-accent-200 dark:hover:bg-dark-800" :disabled="currentPage<=1 || loading" @click="currentPage--; refresh()">上一页</button>
            <button type="button" class="inline-flex h-8 items-center rounded-xl border border-accent-300 bg-white px-3 text-xs font-semibold text-accent-700 transition hover:bg-accent-50 disabled:cursor-not-allowed disabled:opacity-45 dark:border-dark-600 dark:bg-dark-900 dark:text-accent-200 dark:hover:bg-dark-800" :disabled="currentPage*(page?.per_page || 25) >= (page?.total || 0) || loading" @click="currentPage++; refresh()">下一页</button>
          </div>
        </footer>
      </div>
    </div>
    <Teleport to="body">
      <div v-if="selected" class="online-overlay" @pointerdown="detailBackdrop.recordBackdropPointer" @pointerup="detailBackdrop.dismissFromBackdrop" @pointercancel="detailBackdrop.resetBackdropPointer">
        <aside :key="selected.id" role="dialog" aria-modal="true" aria-label="商品详情" class="online-drawer">
          <header class="flex items-center justify-between gap-4">
            <h2 class="text-xl font-bold">商品详情</h2>
            <button class="text-primary-700 dark:text-primary-300" @click="selected=null">关闭 ×</button>
          </header>
          <div class="my-6 flex items-start gap-4">
            <img v-if="thumbnailSrc(selected.thumbnail)" referrerpolicy="no-referrer" :src="thumbnailSrc(selected.thumbnail)" alt="" class="size-20 shrink-0 rounded-lg border border-accent-200/70 object-cover dark:border-dark-600" @error="markThumbnailBroken(selected.thumbnail)" />
            <span v-else class="flex size-20 shrink-0 items-center justify-center rounded-xl border border-accent-200/70 bg-accent-100/80 text-accent-400 dark:border-dark-700 dark:bg-dark-800 dark:text-dark-400" aria-hidden="true"><PhImage :size="26" /></span>
            <div class="min-w-0">
              <h3 class="break-words text-lg font-semibold">{{ selected.title || selected.remote_id }}</h3>
              <p class="muted mt-2 break-all">SKU {{ selected.seller_sku || '未提供' }} · {{ platforms[selected.platform] }}</p>
              <p v-if="selected.platform!=='yandex'" class="muted mt-1">{{ page?.store_name }}</p>
            </div>
          </div>
          <div class="flex flex-wrap items-center gap-3">
            <span :class="badge(listingStatus(selected))">{{ label(listingStatus(selected)) }}</span>
            <OnlineBuyerLinks :links="selected.buyer_links" />
            <button type="button" data-testid="detail-refresh-status" class="ml-auto text-sm text-primary-700 disabled:opacity-50 dark:text-primary-300" :disabled="statusRefreshBlocked(selected)" title="只刷新销售状态和平台反馈" @click="refreshStatus(selected)">{{ checkingStatus.has(selected.id) ? '查询中…' : '刷新状态' }}</button>
          </div>
          <p class="muted mt-2 text-xs">{{ selected.status_checked_at ? `状态查询：${time(selected.status_checked_at)}` : `最近同步：${time(selected.synced_at)}` }}</p>
          <p v-if="statusErrors[selected.id]" role="alert" class="mt-3 text-sm text-rose-700 dark:text-rose-300">{{ statusErrors[selected.id] }}</p>
          <p v-if="detailLoading" role="status" data-testid="detail-loading" class="mt-3 text-sm text-accent-500">正在读取当前商品详情…</p>
          <div v-if="detailError" role="alert" data-testid="detail-error" class="mt-3 space-y-2 text-sm text-rose-700 dark:text-rose-300">
            <p>{{ detailError }}；已有资料已保留。</p>
            <button type="button" class="font-medium text-primary-700 hover:underline dark:text-primary-400" @click="detail(selected)">重新读取详情</button>
          </div>
          <p v-if="detailState(selected)" role="status" class="mt-3 text-sm text-amber-700 dark:text-amber-300">{{ detailState(selected) }}，完成后可修改。</p>
          <p v-for="e in selected.errors" :key="e" role="alert" class="mt-3 text-sm text-rose-700 dark:text-rose-300">{{ e }}</p>

          <section class="online-card my-5 grid grid-cols-1 divide-y divide-accent-200 dark:divide-dark-700 sm:grid-cols-2 sm:divide-x sm:divide-y-0" aria-label="价格与库存" data-testid="detail-sales-summary">
            <div class="min-w-0 space-y-2 p-3">
              <h3 class="muted text-xs">价格</h3>
              <div v-for="p in selected.prices" :key="p.id">
                <p class="truncate text-sm font-medium" :title="`${p.currency} ${p.amount ?? '未知'}`">{{ p.currency }} {{ p.amount ?? '未知' }}</p>
                <p class="muted mt-0.5 truncate text-xs" :title="`${p.label} · ${priceKind(p.kind)}`">{{ p.label }} · {{ priceKind(p.kind) }}</p>
              </div>
              <p v-if="!selected.prices.length" class="muted truncate text-xs">尚未取得价格</p>
            </div>
            <div class="min-w-0 space-y-2 p-3">
              <h3 class="muted text-xs">库存</h3>
              <div v-for="s in selected.stocks" :key="s.id">
                <p class="truncate text-sm font-medium" :title="s.quantity === null ? '未知' : `${s.quantity} 件`">{{ s.quantity ?? '未知' }}<span v-if="s.quantity !== null" class="ml-1 text-xs font-normal">件</span></p>
                <p class="muted mt-0.5 truncate text-xs" :title="s.label">{{ s.label }}</p>
              </div>
              <p v-if="!selected.stocks.length" class="muted truncate text-xs">尚未取得库存</p>
            </div>
          </section>
          <p class="muted text-xs">{{ (selected.sales_checked_at || selected.synced_at) ? `价格与库存查询于 ${time(selected.sales_checked_at || selected.synced_at)}` : '价格与库存尚未读取' }}</p>
          <p v-for="note in salesNotes([...selected.prices, ...selected.stocks])" :key="note" class="muted text-xs">{{ note }}</p>
          <p v-if="cardNotice" role="status" class="mt-3 text-sm text-amber-700 dark:text-amber-300">{{ cardNotice }}</p>
          <OnlinePlatformFeedback :listing="selected" />

          <section v-if="selected.seller_sku" class="online-card my-4 space-y-2 p-3.5" data-testid="online-sku-source" aria-label="1688 采购货源">
            <div class="flex items-center justify-between gap-2">
              <h3 class="text-xs font-semibold text-accent-700 dark:text-accent-200">1688 采购货源</h3>
              <button type="button" data-testid="online-detail-manage-source" class="text-xs font-medium text-primary-700 hover:underline dark:text-primary-300" @click="skuSourceTarget = selected">
                {{ listingSkuBindings(selected)?.length ? '更换 / 管理货源' : '查看 / 绑定 1688 规格' }}
              </button>
            </div>
            <template v-if="listingDefaultSource(selected)">
              <p class="break-words text-sm font-medium text-accent-950 dark:text-white">{{ listingDefaultSource(selected)!.source.specification }}</p>
              <p class="muted text-xs">
                <span v-if="listingDefaultSource(selected)!.source.supplier">{{ listingDefaultSource(selected)!.source.supplier }} · </span>
                Offer {{ listingDefaultSource(selected)!.source.source_offer_id || '—' }} · SKU {{ listingDefaultSource(selected)!.source.source_sku_id || '—' }}
                <span v-if="(listingSkuBindings(selected)?.length || 0) > 1"> · 另含 {{ listingSkuBindings(selected)!.length - 1 }} 个备选</span>
              </p>
            </template>
            <p v-else-if="listingSkuBindings(selected)" class="muted text-xs">尚未绑定 1688 采购源；提前绑定后，该 SKU 出单即可直接自动采购。</p>
            <p v-else class="muted text-xs">可提前查看、绑定或更换该在售 SKU 的 1688 默认及备选采购货源，出单时直接匹配自动采购。</p>
          </section>

          <div class="my-5 flex flex-wrap items-start gap-3" data-testid="detail-actions">
            <button v-for="op in ['price','stock','content'] as const" :key="op" class="btn btn-outline" :disabled="checkingStatus.has(selected.id) || detailLoading || !!detailError || selected.details_state!=='ready' || !!selected.errors.length || !selected.capabilities[op]?.enabled" :title="selected.capabilities[op]?.reason" @click="begin(op)">{{ label(op) }}</button>
            <button class="btn ml-auto text-accent-500 dark:text-accent-300" :disabled="selected.archived || checkingStatus.has(selected.id) || detailLoading || !!detailError || selected.details_state!=='ready' || !!selected.errors.length || !selected.capabilities.sale_state?.enabled" :title="selected.archived ? '请先恢复归档，再调整销售状态' : selected.capabilities.sale_state?.reason" @click="begin('sale_state')">{{ saleState==='active'?'恢复销售':'停售商品' }}</button>
            <button v-if="selected.platform==='yandex'" class="btn btn-outline" :disabled="detailLoading || !!detailError || checkingStatus.has(selected.id) || !selected.capabilities.archive?.enabled" @click="begin('archive')">{{ selected.archived ? '恢复归档' : '归档商品' }}</button>
          </div>
          <template v-for="(cap,op) in selected.capabilities" :key="op">
            <p v-if="cap && !cap.enabled && cap.reason" class="muted mb-2 text-xs">{{ label(op) }}：{{ cap.reason }}</p>
          </template>

          <details v-if="selected.markets.length" class="my-6 border-t border-accent-200 pt-4 dark:border-dark-700">
            <summary class="cursor-pointer text-sm font-semibold">销售市场 · {{ selected.markets.length }} 个</summary>
            <div class="online-card mt-3 overflow-x-auto">
              <table class="w-full text-left text-sm">
                <thead><tr><th>销售市场</th><th>价格 / 币种</th><th>销售状态</th></tr></thead>
                <tbody><tr v-for="m in selected.markets" :key="m.id"><td>{{ m.name || m.site_id }}<span v-if="m.logistic_type"> · {{ m.logistic_type }}</span></td><td>{{ m.currency }} {{ m.price ?? '未知' }}</td><td>{{ label(m.raw_status) }}</td></tr></tbody>
              </table>
            </div>
          </details>
          <OnlineProductDetails :listing="selected" />
          <details class="mt-6 border-t border-accent-200 pt-4 dark:border-dark-700" data-testid="platform-details">
            <summary class="cursor-pointer text-sm text-accent-500 dark:text-accent-300">平台与同步详情</summary>
            <div class="muted mt-4 space-y-3 break-words">
              <p v-if="selected.basic_synced_at">基础同步：{{ time(selected.basic_synced_at) }}</p><p>详情读取：{{ time(selected.synced_at) }}</p>
              <p>状态查询：{{ selected.status_checked_at ? time(selected.status_checked_at) : '尚未查询' }}</p>
              <p>刷新状态只更新销售状态及平台反馈；重新打开详情会读取当前商品的详细数据。</p>
              <p>卡片状态：{{ selected.raw_sub_status.map(label).join('、') || '平台未提供' }}</p>
              <p class="break-all">{{ selected.platform==='yandex' ? '仓库状态汇总' : '平台原始状态' }}：{{ selected.raw_status }} {{ selected.raw_sub_status.join('、') }}</p>
              <p class="break-all">平台商品 ID：{{ selected.remote_id }}</p>
              <p class="break-all">商品模型：{{ selected.model }}</p>
              <p class="break-all">店铺账号：{{ selected.account_id }}</p>
              <p v-for="m in selected.markets" :key="m.id" class="break-all">{{ m.name || m.site_id }} · 市场 ID：{{ m.id }} · {{ m.raw_status }}</p>
              <template v-for="(issue,index) in selected.platform_issues" :key="index">
                <p v-if="issue.code">平台代码：{{ issue.code }} · {{ issue.message }}</p>
              </template>
              <dl v-if="selected.content.attributes?.length" class="space-y-2">
                <div v-for="attribute in selected.content.attributes" :key="String(attribute.id)">
                  <dt>{{ onlineAttributeName(attribute) }}</dt>
                  <dd class="text-xs">属性编号：{{ attribute.id }}</dd>
                </div>
              </dl>
            </div>
          </details>
        </aside>
      </div>
      <div v-if="modal" class="online-overlay online-modal-layer" @pointerdown="modalBackdrop.recordBackdropPointer" @pointerup="modalBackdrop.dismissFromBackdrop" @pointercancel="modalBackdrop.resetBackdropPointer">
        <section role="dialog" aria-modal="true" :aria-label="modal==='sync'?'同步商品':label(modal)" class="online-modal space-y-5" :class="modal==='content' && !preview ? 'online-content-form' : ''">
          <header class="flex justify-between"><h2 class="text-xl font-bold">{{ modal==='sync'?'同步商品':preview?'确认本次变更':label(modal) }}</h2><button :disabled="busy" @click="modal=''">关闭 ×</button></header>
          <div v-if="error" role="alert" class="space-y-2 text-rose-700 dark:text-rose-300">
            <p>{{ error }}</p>
            <button v-if="conflictJobId" type="button" class="btn btn-outline" :disabled="busy" data-testid="online-conflict-operation" @click="emit('open-task', conflictJobId)">查看对应任务</button>
          </div>
          <template v-if="modal==='sync'">
            <p class="break-words">同步 {{ platforms[platform] }} {{ platform==='yandex' ? '账号商品（包含全部仓库对应店铺）' : `店铺：${page?.store_name}` }}</p>
            <div class="rounded-lg border border-accent-200 bg-accent-50 p-5 text-accent-800 dark:border-dark-700 dark:bg-dark-800 dark:text-accent-100">
              <b>同步范围</b>
              <p class="mt-3 leading-relaxed">{{ platform === 'yandex' ? (syncCatalog === 'archived' ? '仅同步当前账号的归档商品。' : '仅同步当前账号的普通商品，包括在售、停售、缺货及审核中的商品。') : '当前账号全部可读取商品及所有关联市场。' }}</p>
            </div>
            <p class="muted">{{ platform==='yandex' ? '全量同步会读取目录图文、卡片反馈并清理平台已删除商品；日常仅更新价格、库存与销售状态可直接使用顶部「刷新价格与库存」。' : '全量同步会扫描店铺全部商品以发现新增或已删除项；日常刷新已有商品可直接使用顶部「刷新价格与库存」。' }}</p>
            <p class="muted">此操作只读取平台商品。部分失败会保留已有快照；离开页面后后台继续同步。</p>
            <button class="btn btn-primary w-full" :disabled="busy" @click="submit">{{ busy ? '正在提交…' : '开始同步' }}</button>
          </template>
          <template v-else-if="selected">
            <p class="font-medium">{{ selected.title }}</p><p class="muted">作用范围：{{ modal==='price' ? price?.label : modal==='stock' ? stock?.label : modal==='sale_state' && saleMarket ? saleMarket.name || saleMarket.site_id : selected.capabilities[modal]?.scope }}</p>
            <p v-if="selected.capabilities[modal]?.reason" class="muted">{{ selected.capabilities[modal]?.reason }}</p>
            <p v-if="modal==='archive'" class="rounded-lg bg-amber-50 p-5 text-amber-800 dark:bg-amber-950/30 dark:text-amber-200">{{ archiveTarget ? '归档后移入归档列表，并停止在此商家账号的所有店铺展示。原库存与停售设置保留，可以恢复归档。' : '恢复后移回普通商品列表，原停售设置与库存保留；满足平台库存、价格及审核条件后才可销售。' }}</p>
            <p v-if="modal==='stock' && stock?.reason" class="muted">{{ stock.reason }}</p>
            <p v-if="modal==='price' && price?.reason" class="muted">{{ price.reason }}</p>
            <div v-if="modal==='content'" v-show="!preview" key="content-editor"><OnlineContentEditor :key="selected.id" :listing="selected" @change="contentChanges=$event" @picture-previews="contentPicturePreviews=$event" @validity="contentValid=$event" @attribute-names="contentAttributeNames=$event" /></div>
            <template v-if="!preview">
              <template v-if="modal==='price'"><label class="block">价格范围<select v-model="scope" class="input mt-2" @change="resetValue"><option v-for="p in selected.prices" :key="p.id" :value="p.id" :disabled="!p.writable">{{ p.label }} · {{ priceKind(p.kind) }} · {{ p.currency }}</option></select></label><p class="muted">当前：{{ price?.currency }} {{ price?.amount }} · {{ priceKind(price?.kind || '') }}</p><label class="block">新价格（{{ price?.currency }}）<input v-model="value" class="input mt-2" inputmode="decimal" /></label></template>
              <template v-if="modal==='stock'"><label class="block">库存范围<select v-model="scope" class="input mt-2" @change="resetValue"><option v-for="s in selected.stocks" :key="s.id" :value="s.id" :disabled="!s.writable">{{ s.label }}</option></select></label><p class="muted">当前库存：{{ stock?.quantity }}。设置绝对数量，不是增减量。</p><label class="block">目标可售数量<input v-model="value" type="number" min="0" step="1" class="input mt-2" /></label></template>
              <template v-if="modal==='sale_state'"><label v-if="selected.platform==='yandex' && page?.warehouses?.length" class="mb-4 block">仓库店铺<select v-model="scope" class="input mt-2" @change="saleTarget=saleState"><option disabled value="">请选择仓库店铺</option><option v-for="saleOption in selected.markets" :key="saleOption.id" :value="`campaign:${saleOption.id}`">{{ saleOption.name || saleOption.site_id }}</option></select></label><label class="block">目标销售状态<select v-model="saleTarget" class="input mt-2"><option value="paused">停售（已暂停时可重新确认主动停售）</option><option value="active">恢复销售</option></select></label><p class="rounded-xl bg-amber-50 p-4 text-amber-800 dark:bg-amber-500/10 dark:text-amber-200">{{ saleTarget==='paused'?'主动停售会影响上述范围内的销售市场，库存同步不会取消停售意图。':'恢复请求仍须满足平台库存、审核及账号限制；提交成功不代表已经恢复在售。' }}</p><p>{{ (saleMarket ? [saleMarket] : selected.markets).map(m=>m.name || `${m.site_id} ${m.logistic_type}`).join('、') }}</p></template>
              <button class="btn btn-primary w-full" @click="makePreview">预览变更</button>
            </template>
            <template v-else><div v-for="(next,field) in changes" :key="field" class="rounded-lg border p-4"><b>{{ fieldNames[field] || field }}</b><p class="muted mt-2 break-all">原：{{ displayValue(field, beforeValue(field)) }}</p><p class="mt-2 break-all text-primary-700">新：{{ displayValue(field, next) }}</p><div v-if="field==='pictures'" class="mt-3 flex flex-wrap gap-2"><OnlinePicturePreview v-for="(url,index) in contentPicturePreviews" :key="index" :src="url" :alt="`目标图片 ${index+1}`" class="size-16" /></div></div><p class="muted">提交前将重新读取平台字段；发生并发变化时会停止修改。</p><div class="flex gap-3"><button class="btn btn-outline" :disabled="busy" @click="preview=false">返回编辑</button><button class="btn btn-primary flex-1" :disabled="busy" @click="submit">{{ busy?'正在提交…':'确认提交' }}</button></div></template>
          </template>
        </section>
      </div>
    </Teleport>
    <OzonExportDialog v-if="showOzonExport" :open="showOzonExport" :listing-ids="exportListingIds" :account-id="page?.account_id || ''" @close="showOzonExport=false" />
    <SkuSourceManagerDialog
      v-if="skuSourceTarget && skuSourceTarget.seller_sku"
      :platform="skuSourceTarget.platform"
      :account-id="skuSourceTarget.account_id"
      :seller-sku="skuSourceTarget.seller_sku"
      :remote-id="skuSourceTarget.platform === 'yandex' ? '' : skuSourceTarget.remote_id"
      :title="skuSourceTarget.title || skuSourceTarget.seller_sku"
      :initial-bindings="listingSkuBindings(skuSourceTarget)"
      @close="skuSourceTarget = null"
      @updated="handleOnlineSkuSourcesUpdated(skuSourceTarget!, $event)"
    />
    <WorkspaceDialog :open="!!batchSelection" :title="batchSelection?.title || '批量操作'" :close-disabled="busy" width="640px" data-testid="online-batch-dialog" @close="batchSelection=null">
      <template v-if="batchSelection">
        <p class="mb-3">共 {{ batchSelection.preview.product_count }} 个商品、{{ batchSelection.preview.items.length }} 个 SKU，作用范围：{{ batchSelection.preview.scope }}。</p>
        <p class="muted mb-4">勾选商品父行包含该目录下的全部规格，不受搜索和销售状态筛选影响；单独勾选 SKU 仅处理该规格。</p>
        <p v-if="batchSelection.operation==='archive'" class="mb-4 text-sm">{{ batchSelection.changes.archived ? '成功后移入归档列表，原库存和停售设置保留。平台仓仍有实物库存的商品可能无法归档。' : '成功后移回普通商品列表，原库存和停售设置保留；恢复归档不保证立即可售。' }}</p>
        <p v-else class="mb-4 text-sm">{{ batchSelection.changes.state==='paused' ? '停售会隐藏当前店铺中的所选商品，库存保持不变。' : '恢复会解除当前店铺的隐藏设置，实际可售仍受库存和平台审核限制。' }}</p>
        <ul class="mb-5 max-h-64 space-y-3 overflow-y-auto"><li v-for="entry in batchSelection.preview.items" :key="entry.listing_id">{{ entry.seller_sku }} · {{ entry.title }}</li></ul>
        <div v-if="batchSelection.preview.errors.length" role="alert" class="mb-4 text-sm text-rose-600"><p>以下商品暂不能执行，请调整选择后重新预览：</p><p v-for="entry in batchSelection.preview.errors" :key="entry.listing_id">{{ entry.seller_sku }}：{{ entry.error }}</p></div>
        <p v-if="batchError" role="alert" class="mb-4 text-sm text-rose-600">{{ batchError }}</p>
        <button class="btn btn-primary w-full" :disabled="busy || !!batchSelection.preview.errors.length" @click="submitSelection">{{ busy ? '正在提交…' : `确认${batchSelection.title}` }}</button>
      </template>
    </WorkspaceDialog>
  </section>
</template>

<style scoped>
.seg-tab {
  @apply inline-flex h-8 items-center rounded-lg px-3 text-xs font-medium text-accent-600 transition hover:text-accent-950 disabled:cursor-not-allowed disabled:opacity-50 dark:text-accent-300 dark:hover:text-white;
}
.seg-tab[aria-selected='true'] {
  @apply bg-primary-600 font-semibold text-white shadow-sm dark:bg-primary-600 dark:text-white;
}
.summary-chip {
  @apply inline-flex items-baseline gap-1.5 rounded-lg border border-transparent px-2.5 py-1 transition hover:bg-accent-100/80 disabled:cursor-default disabled:opacity-60 disabled:hover:bg-transparent dark:hover:bg-white/5;
}
.summary-chip[data-active='true'] {
  @apply border-primary-200 bg-primary-50/80 dark:border-primary-500/35 dark:bg-primary-500/15;
}
.filter-chip {
  @apply inline-flex h-7 items-center gap-1 rounded-lg border border-accent-200/90 bg-white pl-2 pr-1 text-xs transition hover:border-accent-300 dark:border-dark-600 dark:bg-dark-900 dark:hover:border-dark-500;
}
.filter-chip[data-active='true'] {
  @apply border-primary-400/80 bg-primary-50/70 dark:border-primary-500/50 dark:bg-primary-500/10;
}
.filter-label {
  @apply shrink-0 select-none text-[11px] font-medium text-accent-500 dark:text-accent-400;
}
.filter-chip[data-active='true'] .filter-label {
  @apply text-primary-700 dark:text-primary-300;
}
.filter-select {
  @apply h-full cursor-pointer border-0 bg-transparent py-0 pl-0.5 pr-1 text-xs font-medium text-accent-900 focus:outline-none focus:ring-0 disabled:cursor-not-allowed disabled:opacity-50 dark:text-accent-100;
}
.sync-pill {
  @apply inline-flex items-center gap-1.5 rounded-full bg-accent-100 px-2.5 py-0.5 text-[11px] font-semibold text-accent-700 dark:bg-dark-700 dark:text-accent-200;
}
.sync-dot {
  @apply size-1.5 shrink-0 rounded-full bg-accent-400;
}
.sync-pill[data-tone='success'] {
  @apply bg-primary-100/90 text-primary-800 dark:bg-primary-500/15 dark:text-primary-300;
}
.sync-pill[data-tone='success'] .sync-dot {
  @apply bg-primary-500;
}
.sync-pill[data-tone='danger'] {
  @apply bg-rose-100/90 text-rose-800 dark:bg-rose-500/15 dark:text-rose-300;
}
.sync-pill[data-tone='danger'] .sync-dot {
  @apply bg-rose-500;
}
.sync-pill[data-tone='warning'] {
  @apply bg-amber-100/90 text-amber-800 dark:bg-amber-500/15 dark:text-amber-300;
}
.sync-pill[data-tone='warning'] .sync-dot {
  @apply bg-amber-500;
}
.sync-pill[data-tone='info'] {
  @apply bg-blue-100/90 text-blue-800 dark:bg-blue-500/15 dark:text-blue-300;
}
.sync-pill[data-tone='info'] .sync-dot {
  @apply bg-blue-500;
}
.online-action { @apply w-full rounded-lg px-3 py-2 text-left text-xs font-medium text-accent-800 transition hover:bg-accent-100/80 disabled:cursor-not-allowed disabled:opacity-45 dark:text-accent-100 dark:hover:bg-white/5; }
.online-card { @apply rounded-xl border border-accent-200 bg-white dark:border-dark-700 dark:bg-dark-900; }
.online-group { @apply bg-primary-50/45 dark:bg-primary-500/[0.06]; }
.online-child { @apply bg-accent-50/35 dark:bg-dark-950/35; }
.online-overlay { @apply fixed inset-0 z-50 flex justify-end bg-black/35; }
.online-drawer { @apply h-full w-full max-w-[760px] overflow-y-auto bg-white p-5 shadow-xl dark:bg-dark-900 sm:p-6; }
.online-modal-layer { @apply z-[60] items-center justify-center p-4; }
.online-modal { @apply max-h-[90vh] w-full max-w-[640px] overflow-y-auto rounded-2xl bg-white p-6 shadow-xl dark:bg-dark-900; }
.online-content-form { @apply max-w-[1000px]; }
</style>
