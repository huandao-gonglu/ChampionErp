<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  PhArrowSquareOut,
  PhCheckCircle,
  PhLink,
  PhMagnifyingGlass,
  PhStar,
  PhTrash,
} from '@phosphor-icons/vue'
import WorkspaceDialog from '@/components/shared/WorkspaceDialog.vue'
import {
  bindSkuSource,
  linkMasterSku,
  querySkuSources,
  removeSkuSource,
  resolveAlibabaSource,
  setDefaultSkuSource,
} from '@/api/skuSources'
import type {
  OrderDetail,
  OrderPlatform,
  ResolvedAlibabaProduct,
  SalesSkuBinding,
} from '@/types/orders'

const props = defineProps<{
  platform: OrderPlatform
  accountId?: string
  sellerSku: string
  remoteId?: string
  variantId?: string
  title?: string
  initialBindings?: SalesSkuBinding[]
  orderId?: string
  lineKey?: string
  revision?: number
}>()

const emit = defineEmits<{
  close: []
  updated: [payload: { bindings: SalesSkuBinding[]; orderDetail?: OrderDetail }]
}>()

const bindings = ref<SalesSkuBinding[]>(props.initialBindings ? [...props.initialBindings] : [])
const masterSku = ref(props.sellerSku)
const linkedMasterSku = ref('')
const masterSkuDraft = ref('')
const editingMasterSku = ref(false)
const sharedAcrossPlatforms = ref(true)
const loadingBindings = ref(false)
const busy = ref(false)
const resolving = ref(false)
const error = ref('')
const notice = ref('')
const urlInput = ref('')
const resolved = ref<ResolvedAlibabaProduct | null>(null)
const selectedSkuId = ref('')
const skuSearch = ref('')

const platformNames: Record<OrderPlatform, string> = {
  mercadolibre: 'Mercado Libre',
  ozon: 'Ozon',
  yandex: 'Yandex Market',
}

const filteredResolvedSkus = computed(() => {
  const list = resolved.value?.skus || []
  const keyword = skuSearch.value.trim().toLowerCase()
  if (!keyword) return list
  return list.filter((sku) =>
    `${sku.specification} ${sku.source_sku_id} ${sku.source_spec_id}`.toLowerCase().includes(keyword),
  )
})

const selectedResolvedSku = computed(() =>
  (resolved.value?.skus || []).find((sku) => sku.source_sku_id === selectedSkuId.value) || null,
)

function defaultBindingId() {
  const explicit = bindings.value.find((item) => item.is_default)
  if (explicit) return explicit.id
  return bindings.value.length === 1 ? bindings.value[0].id : ''
}

function bindingOriginLabel(binding: SalesSkuBinding) {
  return binding.origin === 'alibaba_api' ? '1688 核验' : '采集发布'
}

async function refreshBindings() {
  if (!props.sellerSku) return
  loadingBindings.value = true
  try {
    const items = await querySkuSources([
      {
        platform: props.platform,
        account_id: props.accountId || '',
        seller_sku: props.sellerSku,
        remote_id: props.remoteId || '',
        variant_id: props.variantId || '',
      },
    ])
    if (items[0]) {
      bindings.value = items[0].bindings || []
      masterSku.value = items[0].master_sku || props.sellerSku
      linkedMasterSku.value = items[0].linked_master_sku || ''
      masterSkuDraft.value = linkedMasterSku.value || masterSku.value
      if (!props.orderId) emit('updated', { bindings: bindings.value })
    }
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : '读取当前 SKU 采购源失败'
  } finally {
    loadingBindings.value = false
  }
}

async function resolveLink() {
  const raw = urlInput.value.trim()
  if (!raw || resolving.value || busy.value) return
  resolving.value = true
  error.value = ''
  notice.value = ''
  try {
    const data = await resolveAlibabaSource(raw)
    resolved.value = data
    skuSearch.value = ''
    const firstValid = data.skus.find((sku) => sku.can_auto_purchase)
    selectedSkuId.value = firstValid ? firstValid.source_sku_id : ''
  } catch (exc) {
    resolved.value = null
    selectedSkuId.value = ''
    error.value = exc instanceof Error ? exc.message : '解析 1688 商品失败，请检查链接或 1688 授权'
  } finally {
    resolving.value = false
  }
}

async function saveBinding(setDefault: boolean) {
  if (!resolved.value || !selectedResolvedSku.value || !selectedResolvedSku.value.can_auto_purchase || busy.value) return
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    const res = await bindSkuSource({
      platform: props.platform,
      account_id: props.accountId || '',
      seller_sku: props.sellerSku,
      remote_id: props.remoteId || '',
      variant_id: props.variantId || '',
      resolve_id: resolved.value.resolve_id,
      source_sku_id: selectedResolvedSku.value.source_sku_id,
      set_default: setDefault,
      shared_across_platforms: sharedAcrossPlatforms.value,
      master_sku: masterSku.value,
      ...(props.orderId && props.lineKey && setDefault
        ? { order_id: props.orderId, line_key: props.lineKey, revision: props.revision }
        : {}),
    })
    bindings.value = res.bindings || []
    if (res.master_sku) masterSku.value = res.master_sku
    notice.value = setDefault
      ? (sharedAcrossPlatforms.value
        ? `已设为统一货号 ${masterSku.value} 的全平台默认采购源，后续各平台新订单将自动使用此货源。`
        : '已设为当前店铺默认采购源，后续新订单将自动使用此货源。')
      : '已添加为备选采购源。'
    resolved.value = null
    selectedSkuId.value = ''
    urlInput.value = ''
    emit('updated', { bindings: bindings.value, orderDetail: res.order_detail })
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : '保存采购源失败'
  } finally {
    busy.value = false
  }
}

async function switchDefault(binding: SalesSkuBinding) {
  if (busy.value) return
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    const res = await setDefaultSkuSource({
      platform: props.platform,
      account_id: props.accountId || '',
      seller_sku: props.sellerSku,
      remote_id: props.remoteId || '',
      variant_id: props.variantId || '',
      binding_id: binding.id,
      scope: binding.scope || 'store',
      master_sku: binding.master_sku || masterSku.value,
      ...(props.orderId && props.lineKey
        ? { order_id: props.orderId, line_key: props.lineKey, revision: props.revision }
        : {}),
    })
    bindings.value = res.bindings || []
    notice.value = binding.scope === 'master'
      ? `已切换为统一货号 ${masterSku.value} 的全平台默认采购源。`
      : '已切换为该销售 SKU 默认采购源。'
    emit('updated', { bindings: bindings.value, orderDetail: res.order_detail })
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : '切换默认采购源失败'
  } finally {
    busy.value = false
  }
}

async function saveMasterSkuLink() {
  if (busy.value) return
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    const res = await linkMasterSku({
      platform: props.platform,
      account_id: props.accountId || '',
      seller_sku: props.sellerSku,
      master_sku: masterSkuDraft.value.trim(),
    })
    masterSku.value = res.master_sku || props.sellerSku
    linkedMasterSku.value = res.linked_master_sku || ''
    masterSkuDraft.value = linkedMasterSku.value || masterSku.value
    bindings.value = res.bindings || []
    editingMasterSku.value = false
    notice.value = linkedMasterSku.value
      ? `已关联至统一货号 ${masterSku.value}，将共享该货号下的全平台 1688 采购源。`
      : '已恢复默认货号关联。'
    emit('updated', { bindings: bindings.value })
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : '更新统一货号关联失败'
  } finally {
    busy.value = false
  }
}

async function removeBinding(binding: SalesSkuBinding) {
  if (busy.value) return
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    const res = await removeSkuSource({
      platform: props.platform,
      account_id: props.accountId || '',
      seller_sku: props.sellerSku,
      binding_id: binding.id,
      scope: binding.scope || 'store',
      master_sku: binding.master_sku || masterSku.value,
    })
    bindings.value = res.bindings || []
    notice.value = '已移除该采购源（不影响历史采购记录）。'
    emit('updated', { bindings: bindings.value })
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : '移除采购源失败'
  } finally {
    busy.value = false
  }
}

onMounted(() => {
  void refreshBindings()
})
</script>

<template>
  <WorkspaceDialog
    :open="true"
    title="1688 采购货源管理"
    width="760px"
    :close-disabled="busy || resolving"
    @close="emit('close')"
  >
    <div class="space-y-5 text-sm text-accent-800 dark:text-accent-100">
      <div class="space-y-2 rounded-xl border border-accent-200/90 bg-accent-50/80 px-3.5 py-2.5 dark:border-dark-700 dark:bg-dark-800/70">
        <p class="font-semibold text-accent-950 dark:text-white">{{ title || sellerSku }}</p>
        <div class="flex flex-wrap items-center justify-between gap-2 text-xs text-accent-500 dark:text-accent-400">
          <p>
            {{ platformNames[platform] }} · 销售 SKU：<span class="font-mono text-accent-700 dark:text-accent-200">{{ sellerSku }}</span>
            <span class="mx-1.5 text-accent-300 dark:text-dark-600">|</span>
            统一货号：<span class="font-mono font-medium text-primary-700 dark:text-primary-300" data-testid="sku-source-master-sku">{{ masterSku }}</span>
          </p>
          <button
            type="button"
            data-testid="sku-source-edit-master"
            class="text-xs font-medium text-primary-700 hover:underline dark:text-primary-300"
            @click="editingMasterSku = !editingMasterSku"
          >
            {{ editingMasterSku ? '收起' : '关联统一货号' }}
          </button>
        </div>
        <form v-if="editingMasterSku" class="flex flex-wrap items-center gap-2 border-t border-accent-200/70 pt-2 dark:border-dark-700" @submit.prevent="saveMasterSkuLink">
          <input
            v-model="masterSkuDraft"
            data-testid="sku-source-master-input"
            type="text"
            class="input h-8 min-w-0 flex-1 !rounded-lg !py-0 font-mono text-xs"
            placeholder="输入跨平台共享的统一货号（留空恢复使用当前销售 SKU）"
            :disabled="busy"
          />
          <button
            type="submit"
            data-testid="sku-source-master-save"
            class="btn btn-outline !min-h-8 shrink-0 !rounded-lg !px-2.5 text-xs"
            :disabled="busy"
          >
            保存关联
          </button>
        </form>
      </div>

      <p v-if="error" role="alert" class="rounded-xl border border-rose-200 bg-rose-50 px-3.5 py-2 text-xs font-medium text-rose-700 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-200">
        {{ error }}
      </p>
      <p v-if="notice" role="status" class="rounded-xl border border-primary-200 bg-primary-50 px-3.5 py-2 text-xs font-medium text-primary-800 dark:border-primary-500/30 dark:bg-primary-500/10 dark:text-primary-200">
        {{ notice }}
      </p>

      <!-- 已绑定货源列表（默认 + 备选） -->
      <section class="space-y-2.5" aria-label="已绑定采购源">
        <div class="flex items-center justify-between gap-2">
          <h4 class="text-xs font-semibold uppercase tracking-wide text-accent-500 dark:text-accent-400">
            已绑定货源（{{ bindings.length }}）
          </h4>
          <span v-if="loadingBindings" class="text-xs text-accent-400">加载中…</span>
        </div>

        <div v-if="!bindings.length && !loadingBindings" class="rounded-xl border border-dashed border-accent-200 px-4 py-5 text-center text-xs text-accent-500 dark:border-dark-700 dark:text-accent-400">
          当前销售 SKU 尚未绑定可自动采购的 1688 货源，请在下方粘贴 1688 链接并点选规格完成绑定。
        </div>

        <ul v-else class="space-y-2">
          <li
            v-for="binding in bindings"
            :key="binding.id"
            class="flex flex-wrap items-start justify-between gap-3 rounded-xl border p-3 transition"
            :class="binding.id === defaultBindingId()
              ? 'border-primary-300 bg-primary-50/40 dark:border-primary-500/40 dark:bg-primary-500/10'
              : 'border-accent-200/90 bg-white dark:border-dark-700 dark:bg-dark-900/60'"
          >
            <div class="flex min-w-0 flex-1 items-start gap-3">
              <img
                v-if="binding.image_url"
                :src="binding.image_url"
                referrerpolicy="no-referrer"
                alt=""
                class="size-12 shrink-0 rounded-lg border border-accent-200 object-cover dark:border-dark-700"
              />
              <div class="min-w-0 flex-1 space-y-1">
                <div class="flex flex-wrap items-center gap-1.5">
                  <span
                    v-if="binding.id === defaultBindingId()"
                    class="inline-flex items-center gap-1 rounded-full bg-primary-100 px-2 py-0.5 text-[11px] font-semibold text-primary-800 dark:bg-primary-500/20 dark:text-primary-200"
                  >
                    <PhStar :size="11" weight="fill" aria-hidden="true" />默认货源
                  </span>
                  <span
                    v-else
                    class="rounded-full bg-accent-100 px-2 py-0.5 text-[11px] font-medium text-accent-600 dark:bg-dark-700 dark:text-accent-300"
                  >
                    备选货源
                  </span>
                  <span class="rounded-full bg-accent-100 px-2 py-0.5 text-[11px] text-accent-600 dark:bg-dark-800 dark:text-accent-300">
                    {{ bindingOriginLabel(binding) }}
                  </span>
                  <span
                    class="rounded-full px-2 py-0.5 text-[11px]"
                    :class="binding.scope === 'master'
                      ? 'bg-emerald-100 text-emerald-800 dark:bg-emerald-500/20 dark:text-emerald-200'
                      : 'bg-accent-100 text-accent-600 dark:bg-dark-800 dark:text-accent-300'"
                  >
                    {{ binding.scope === 'master' ? '全平台共享' : '仅本店铺' }}
                  </span>
                  <span v-if="binding.source.supplier" class="truncate text-xs text-accent-500 dark:text-accent-400">
                    {{ binding.source.supplier }}
                  </span>
                </div>
                <p class="break-words font-medium text-accent-950 dark:text-white">
                  {{ binding.source.specification }}
                </p>
                <div class="flex flex-wrap items-center gap-3 text-xs text-accent-500 dark:text-accent-400">
                  <span v-if="binding.source.source_offer_id" class="font-mono">Offer {{ binding.source.source_offer_id }}</span>
                  <span v-if="binding.source.source_sku_id" class="font-mono">SKU {{ binding.source.source_sku_id }}</span>
                  <a
                    :href="binding.source.product_url"
                    target="_blank"
                    rel="noopener noreferrer"
                    class="inline-flex items-center gap-1 text-primary-700 hover:underline dark:text-primary-300"
                  >
                    打开 1688 <PhArrowSquareOut :size="12" aria-hidden="true" />
                  </a>
                </div>
                <p v-if="binding.source.purchase_block_reason" class="text-xs text-rose-600 dark:text-rose-300">
                  {{ binding.source.purchase_block_reason }}
                </p>
              </div>
            </div>
            <div class="flex shrink-0 items-center gap-1.5">
              <button
                v-if="binding.id !== defaultBindingId()"
                type="button"
                data-testid="sku-source-set-default"
                class="btn btn-outline !min-h-8 !rounded-lg !px-2.5 !py-1 text-xs"
                :disabled="busy"
                @click="switchDefault(binding)"
              >
                设为默认
              </button>
              <button
                type="button"
                data-testid="sku-source-remove"
                class="inline-flex size-8 items-center justify-center rounded-lg border border-accent-200 text-accent-500 transition hover:border-rose-200 hover:bg-rose-50 hover:text-rose-600 disabled:opacity-50 dark:border-dark-700 dark:text-accent-400 dark:hover:border-rose-500/30 dark:hover:bg-rose-500/10 dark:hover:text-rose-300"
                title="移除此采购源"
                aria-label="移除此采购源"
                :disabled="busy"
                @click="removeBinding(binding)"
              >
                <PhTrash :size="14" aria-hidden="true" />
              </button>
            </div>
          </li>
        </ul>
      </section>

      <!-- 新增 / 更换 1688 货源 -->
      <section class="space-y-3 rounded-xl border border-accent-200/90 bg-white p-3.5 dark:border-dark-700 dark:bg-dark-900/50" aria-label="绑定新 1688 货源">
        <div class="flex items-center gap-1.5 text-xs font-semibold text-accent-700 dark:text-accent-200">
          <PhLink :size="14" aria-hidden="true" />粘贴 1688 商品链接，拉取真实规格并绑定
        </div>

        <form class="flex flex-wrap gap-2" @submit.prevent="resolveLink">
          <input
            v-model="urlInput"
            data-testid="sku-source-1688-url"
            type="text"
            class="input min-w-0 flex-1 text-sm"
            placeholder="粘贴 1688 商品链接、分享文案或 qr.1688.com 短链"
            :disabled="resolving || busy"
          />
          <button
            type="submit"
            data-testid="sku-source-resolve-btn"
            class="btn btn-primary !min-h-9 shrink-0 !rounded-xl !px-3.5 text-xs"
            :disabled="resolving || busy || !urlInput.trim()"
          >
            {{ resolving ? '正在解析规格…' : '解析 1688 规格' }}
          </button>
        </form>

        <div v-if="resolved" class="space-y-3 border-t border-accent-200/80 pt-3 dark:border-dark-700">
          <div class="flex items-start gap-3">
            <img
              v-if="resolved.image_url"
              :src="resolved.image_url"
              referrerpolicy="no-referrer"
              alt=""
              class="size-12 shrink-0 rounded-lg border border-accent-200 object-cover dark:border-dark-700"
            />
            <div class="min-w-0 flex-1">
              <p class="font-semibold text-accent-950 dark:text-white">{{ resolved.title }}</p>
              <p class="mt-0.5 text-xs text-accent-500 dark:text-accent-400">
                <span v-if="resolved.supplier">{{ resolved.supplier }} · </span>
                Offer {{ resolved.offer_id }} · 共 {{ resolved.skus.length }} 个规格
              </p>
            </div>
          </div>

          <div v-if="resolved.skus.length > 6" class="relative">
            <PhMagnifyingGlass :size="14" class="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-accent-400" aria-hidden="true" />
            <input
              v-model="skuSearch"
              type="search"
              class="input !pl-8 text-xs"
              placeholder="筛选规格名称或 SKU 编号…"
            />
          </div>

          <div class="max-h-60 space-y-1.5 overflow-y-auto pr-1" role="radiogroup" aria-label="选择 1688 规格">
            <label
              v-for="sku in filteredResolvedSkus"
              :key="sku.source_sku_id"
              class="flex cursor-pointer items-center gap-3 rounded-xl border p-2.5 transition"
              :class="!sku.can_auto_purchase
                ? 'cursor-not-allowed border-accent-200/70 bg-accent-50/70 opacity-60 dark:border-dark-700 dark:bg-dark-800/50'
                : selectedSkuId === sku.source_sku_id
                  ? 'border-primary-500 bg-primary-50/50 dark:border-primary-400 dark:bg-primary-500/15'
                  : 'border-accent-200/80 hover:border-accent-300 dark:border-dark-700 dark:hover:border-dark-600'"
            >
              <input
                v-model="selectedSkuId"
                type="radio"
                name="resolved-1688-sku"
                :value="sku.source_sku_id"
                :disabled="!sku.can_auto_purchase || busy"
                class="size-4 accent-primary-600"
              />
              <img
                v-if="sku.image_url"
                :src="sku.image_url"
                referrerpolicy="no-referrer"
                alt=""
                class="size-10 shrink-0 rounded-lg border border-accent-200 object-cover dark:border-dark-700"
              />
              <div class="min-w-0 flex-1">
                <p class="break-words text-xs font-semibold text-accent-900 dark:text-white">{{ sku.specification }}</p>
                <p class="mt-0.5 flex flex-wrap items-center gap-2 text-[11px] text-accent-500 dark:text-accent-400">
                  <span class="font-mono">SKU {{ sku.source_sku_id }}</span>
                  <span v-if="sku.price" class="font-medium text-accent-700 dark:text-accent-200">¥{{ sku.price }}</span>
                  <span v-if="sku.stock">库存 {{ sku.stock }}</span>
                  <span v-if="sku.can_auto_purchase" class="inline-flex items-center gap-0.5 text-primary-700 dark:text-primary-300">
                    <PhCheckCircle :size="11" weight="fill" aria-hidden="true" />可自动采购
                  </span>
                  <span v-else class="text-rose-600 dark:text-rose-300">{{ sku.block_reason }}</span>
                </p>
              </div>
            </label>
          </div>

          <div class="flex flex-wrap items-center justify-between gap-2 pt-1">
            <label class="inline-flex cursor-pointer items-center gap-1.5 text-xs text-accent-700 dark:text-accent-200">
              <input
                v-model="sharedAcrossPlatforms"
                data-testid="sku-source-shared-toggle"
                type="checkbox"
                class="size-4 accent-primary-600"
                :disabled="busy"
              />
              同步应用到全平台同货号 SKU（<span class="font-mono">{{ masterSku }}</span>）
            </label>
            <div class="flex flex-wrap items-center gap-2">
              <button
                type="button"
                data-testid="sku-source-save-backup"
                class="btn btn-outline !min-h-9 !rounded-xl !px-3 text-xs"
                :disabled="busy || !selectedResolvedSku || !selectedResolvedSku.can_auto_purchase"
                @click="saveBinding(false)"
              >
                仅存为备选货源
              </button>
              <button
                type="button"
                data-testid="sku-source-save-default"
                class="btn btn-primary !min-h-9 !rounded-xl !px-3.5 text-xs"
                :disabled="busy || !selectedResolvedSku || !selectedResolvedSku.can_auto_purchase"
                @click="saveBinding(true)"
              >
                {{ busy ? '保存中…' : '绑定并设为默认货源' }}
              </button>
            </div>
          </div>
        </div>
      </section>
    </div>

    <template #footer>
      <div class="flex justify-end">
        <button type="button" class="btn btn-outline !min-h-9 !rounded-xl !px-3.5 text-xs" :disabled="busy || resolving" @click="emit('close')">
          完成
        </button>
      </div>
    </template>
  </WorkspaceDialog>
</template>
