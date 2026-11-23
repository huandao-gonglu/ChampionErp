<script setup lang="ts">
import { onMounted } from 'vue'
import { storeToRefs } from 'pinia'
import {
  PhArrowClockwise,
  PhArrowSquareOut,
  PhCheckCircle,
  PhImage,
  PhLink,
  PhMapPin,
  PhShoppingCart,
  PhWarningCircle,
} from '@phosphor-icons/vue'
import PageHeader from '@/components/layout/PageHeader.vue'
import type { DirectPurchaseAddressFields } from '@/api/alibabaDirectPurchase'
import { useAlibabaDirectPurchaseStore } from '@/stores/alibabaDirectPurchase'

const emit = defineEmits<{ openAuth: [] }>()

const directStore = useAlibabaDirectPurchaseStore()
const {
  state,
  stateLoading,
  stateError,
  urlInput,
  resolving,
  resolvedProduct,
  selectedSkuIds,
  addressMode,
  savedAddresses,
  selectedAddressId,
  addressNotice,
  addressLoading,
  addressError,
  rawAddressText,
  addressParsing,
  addressWarnings,
  manualAddressConfirmed,
  activeRecord,
  payChannel,
  createConfirmed,
  submissionUncertain,
  actionBusy,
  actionError,
  actionNotice,
  recordBusyId,
  agreementLoading,
  agreement,
  payConfirmed,
  canOperate,
  purchasableSkus,
  selectedItems,
  selectedTotalQuantity,
  selectedSavedAddress,
  manualAddressComplete,
  canPreview,
  canCreate,
  activeOrderPaid,
  showProtocolPay,
} = storeToRefs(directStore)

const { skuQuantities, manualFields, cashierUrls } = directStore

const addressEntries: Array<[keyof DirectPurchaseAddressFields, string, number]> = [
  ['fullName', '收货人', 100],
  ['mobile', '手机号', 40],
  ['phone', '固定电话（选填）', 40],
  ['provinceText', '省', 100],
  ['cityText', '市', 100],
  ['areaText', '区 / 县', 100],
  ['townText', '街道 / 乡镇（选填）', 100],
  ['address', '详细地址', 1000],
  ['postCode', '邮编（选填）', 20],
]

const stateLabels: Record<string, string> = {
  preview: '待确认下单',
  submitting: '提交中 / 待核验',
  unknown: '待核验原单',
  created: '已创建',
  failed: '创建失败',
  closed: '已结束',
}

const channelLabels: Record<string, string> = {
  alipay: '支付宝',
  shegou: '先采后付',
}

const paidStatusCodes = new Set([
  'waitsellersend',
  'waitbuyerreceive',
  'waitbuyerreceives',
  'confirm_goods',
  'success',
])

const money = (fen: number) => `¥${(fen / 100).toFixed(2)}`

function formatTime(iso: string): string {
  if (!iso) return ''
  const dt = new Date(iso)
  if (Number.isNaN(dt.getTime())) return iso
  return dt.toLocaleString('zh-CN', { hour12: false })
}

onMounted(() => {
  void directStore.ensureLoaded()
})
</script>

<template>
  <div class="space-y-4">
    <PageHeader
      title="1688 直购"
      description="经万邦企业 Key 直接购买 1688 商品，记录完全独立，不进入采集箱、草稿箱或 ERP 订单工作流。"
    >
      <template #actions>
        <div class="flex flex-wrap items-center gap-2">
          <span
            v-if="state?.session_ready"
            class="inline-flex items-center gap-1.5 rounded-full bg-primary-50 px-3 py-1 text-xs font-medium text-primary-800 dark:bg-primary-500/15 dark:text-primary-200"
          >
            <PhCheckCircle :size="14" weight="fill" aria-hidden="true" />
            万邦买家：{{ state.account_name || state.member_id }}
          </span>
          <button
            v-else
            type="button"
            class="btn btn-outline !min-h-9 !rounded-xl !px-3 !py-1.5 text-sm"
            @click="emit('openAuth')"
          >
            前往平台授权
          </button>
          <button
            type="button"
            class="btn btn-outline !min-h-9 !rounded-xl !px-3 !py-1.5 text-sm"
            :disabled="stateLoading"
            @click="directStore.loadState(savedAddresses.length === 0)"
          >
            <PhArrowClockwise :size="15" aria-hidden="true" />
            {{ stateLoading ? '刷新中…' : '刷新' }}
          </button>
        </div>
      </template>
    </PageHeader>

    <div
      v-if="stateError"
      role="alert"
      class="flex items-start gap-2 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-200"
    >
      <PhWarningCircle :size="18" weight="fill" class="mt-0.5 shrink-0" aria-hidden="true" />
      <span>{{ stateError }}</span>
    </div>

    <div
      v-if="state && state.blocked_reason"
      role="alert"
      class="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200"
    >
      <div class="flex items-start gap-2">
        <PhWarningCircle :size="18" weight="fill" class="mt-0.5 shrink-0" aria-hidden="true" />
        <div>
          <p class="font-semibold">当前暂不能使用 1688 万邦直购</p>
          <p class="mt-0.5">{{ state.blocked_reason }}</p>
        </div>
      </div>
      <button type="button" class="btn btn-primary !min-h-9 !rounded-xl !px-3.5 !py-1.5 text-sm" @click="emit('openAuth')">
        去配置 / 授权万邦 1688
      </button>
    </div>

    <div
      v-if="actionError"
      role="alert"
      class="flex items-start gap-2 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-200"
    >
      <PhWarningCircle :size="18" weight="fill" class="mt-0.5 shrink-0" aria-hidden="true" />
      <span class="min-w-0 break-words">{{ actionError }}</span>
    </div>

    <div
      v-if="actionNotice"
      role="status"
      class="flex items-start gap-2 rounded-xl border border-primary-200 bg-primary-50 px-4 py-3 text-sm text-primary-800 dark:border-primary-500/30 dark:bg-primary-500/10 dark:text-primary-200"
    >
      <PhCheckCircle :size="18" weight="fill" class="mt-0.5 shrink-0" aria-hidden="true" />
      <span>{{ actionNotice }}</span>
    </div>

    <div class="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1.45fr)_minmax(340px,0.95fr)]">
      <!-- 左侧：解析链接、复选 SKU、选择/输入地址、预览并创建订单 -->
      <div class="space-y-4">
        <!-- 1. 商品链接与 SKU 多选 -->
        <section class="rounded-2xl border border-accent-200 bg-white p-4 shadow-sm dark:border-dark-700 dark:bg-dark-900/70">
          <div class="flex items-center gap-2">
            <PhLink :size="18" class="text-primary-600 dark:text-primary-400" aria-hidden="true" />
            <h2 class="text-base font-semibold text-accent-950 dark:text-white">1. 粘贴 1688 链接并勾选规格</h2>
          </div>

          <div class="mt-3 flex flex-col gap-2 sm:flex-row">
            <input
              v-model="urlInput"
              type="text"
              class="input flex-1"
              aria-label="1688 商品链接或分享文案"
              placeholder="粘贴 1688 商品链接、App 分享文案或 qr.1688.com 短链"
              :disabled="!canOperate || resolving || actionBusy"
              @keydown.enter.prevent="directStore.handleResolveProduct"
            />
            <button
              type="button"
              class="btn btn-primary shrink-0 !rounded-xl"
              :disabled="!canOperate || !urlInput.trim() || resolving || actionBusy"
              @click="directStore.handleResolveProduct"
            >
              {{ resolving ? '解析中…' : '解析商品' }}
            </button>
          </div>

          <div v-if="resolvedProduct" class="mt-4 space-y-3 border-t border-accent-200 pt-4 dark:border-dark-700">
            <div class="flex items-start gap-3">
              <img
                v-if="directStore.displayImage(resolvedProduct.main_image)"
                :src="directStore.displayImage(resolvedProduct.main_image)"
                referrerpolicy="no-referrer"
                loading="lazy"
                alt=""
                class="size-14 shrink-0 rounded-xl border border-accent-200 object-cover dark:border-dark-700"
                @error="directStore.markImageBroken(resolvedProduct.main_image)"
              />
              <div
                v-else
                class="grid size-14 shrink-0 place-items-center rounded-xl border border-accent-200 bg-accent-100 text-accent-400 dark:border-dark-700 dark:bg-dark-800"
                aria-hidden="true"
              >
                <PhImage :size="20" />
              </div>
              <div class="min-w-0 flex-1">
                <a
                  :href="resolvedProduct.product_url"
                  target="_blank"
                  rel="noopener noreferrer"
                  class="inline-flex items-center gap-1 font-semibold text-accent-950 hover:text-primary-700 hover:underline dark:text-white dark:hover:text-primary-300"
                >
                  <span class="break-words">{{ resolvedProduct.title }}</span>
                  <PhArrowSquareOut :size="14" class="shrink-0" aria-hidden="true" />
                </a>
                <p class="mt-0.5 text-xs text-accent-500 dark:text-accent-400">
                  商品编号：{{ resolvedProduct.offer_id }}
                  <span v-if="resolvedProduct.supplier"> · 商家：{{ resolvedProduct.supplier }}</span>
                </p>
              </div>
            </div>

            <div class="flex flex-wrap items-center justify-between gap-2 text-xs text-accent-600 dark:text-accent-300">
              <span>已勾选 <strong>{{ selectedItems.length }}</strong> 个规格，共 <strong>{{ selectedTotalQuantity }}</strong> 件</span>
              <div class="flex items-center gap-2">
                <button
                  type="button"
                  class="btn btn-outline !min-h-7 !rounded-lg !px-2.5 !py-1 text-xs"
                  :disabled="!purchasableSkus.length || actionBusy"
                  @click="directStore.selectAllPurchasable"
                >
                  全选可购规格
                </button>
                <button
                  type="button"
                  class="btn btn-outline !min-h-7 !rounded-lg !px-2.5 !py-1 text-xs"
                  :disabled="!selectedSkuIds.length || actionBusy"
                  @click="directStore.clearSkuSelection"
                >
                  清空勾选
                </button>
              </div>
            </div>

            <p v-if="!resolvedProduct.skus.length" class="rounded-xl bg-amber-50 p-3 text-sm text-amber-800 dark:bg-amber-500/10 dark:text-amber-200">
              该商品未返回可下单的 SKU 规格，暂不支持通过万邦 API 直购。
            </p>

            <div v-else class="max-h-80 overflow-auto rounded-xl border border-accent-200 dark:border-dark-700">
              <table class="min-w-full divide-y divide-accent-200 text-sm dark:divide-dark-700">
                <thead class="bg-accent-50 text-left text-xs font-semibold text-accent-600 dark:bg-dark-800/80 dark:text-accent-300">
                  <tr>
                    <th class="w-10 px-3 py-2">选</th>
                    <th class="px-3 py-2">规格 / SKU</th>
                    <th class="w-24 px-3 py-2">参考价</th>
                    <th class="w-20 px-3 py-2">库存</th>
                    <th class="w-28 px-3 py-2">购买数量</th>
                  </tr>
                </thead>
                <tbody class="divide-y divide-accent-100 dark:divide-dark-800">
                  <tr
                    v-for="sku in resolvedProduct.skus"
                    :key="sku.sku_id"
                    :class="directStore.isSkuChecked(sku.sku_id) ? 'bg-primary-50/50 dark:bg-primary-500/10' : ''"
                  >
                    <td class="px-3 py-2.5 align-middle">
                      <input
                        type="checkbox"
                        :checked="directStore.isSkuChecked(sku.sku_id)"
                        :disabled="!sku.purchasable || actionBusy"
                        :aria-label="`选择规格 ${sku.specification}`"
                        @change="directStore.toggleSku(sku.sku_id, ($event.target as HTMLInputElement).checked)"
                      />
                    </td>
                    <td class="px-3 py-2.5 align-middle">
                      <div class="flex items-center gap-2.5">
                        <img
                          v-if="directStore.displayImage(sku.image)"
                          :src="directStore.displayImage(sku.image)"
                          referrerpolicy="no-referrer"
                          loading="lazy"
                          alt=""
                          class="size-9 shrink-0 rounded-lg border border-accent-200 object-cover dark:border-dark-700"
                          @error="directStore.markImageBroken(sku.image)"
                        />
                        <div
                          v-else
                          class="grid size-9 shrink-0 place-items-center rounded-lg border border-accent-200 bg-accent-100 text-accent-400 dark:border-dark-700 dark:bg-dark-800"
                          aria-hidden="true"
                        >
                          <PhImage :size="15" />
                        </div>
                        <div class="min-w-0">
                          <p class="font-medium text-accent-950 dark:text-white">{{ sku.specification }}</p>
                          <p class="text-xs text-accent-500 dark:text-accent-400">SKU: {{ sku.sku_id }}</p>
                          <p v-if="sku.block_reason" class="text-xs text-amber-700 dark:text-amber-300">{{ sku.block_reason }}</p>
                        </div>
                      </div>
                    </td>
                    <td class="px-3 py-2.5 align-middle tabular-nums text-accent-900 dark:text-accent-100">
                      {{ sku.price ? `¥${sku.price}` : '以预览为准' }}
                    </td>
                    <td class="px-3 py-2.5 align-middle tabular-nums text-xs text-accent-600 dark:text-accent-300">
                      {{ sku.stock || '—' }}
                    </td>
                    <td class="px-3 py-2.5 align-middle">
                      <input
                        type="number"
                        min="1"
                        max="10000"
                        step="1"
                        class="input !min-h-8 w-24 !px-2 !py-1 text-sm tabular-nums"
                        :value="skuQuantities[sku.sku_id] ?? 1"
                        :disabled="!sku.purchasable || actionBusy"
                        :aria-label="`${sku.specification} 购买数量`"
                        @input="directStore.updateSkuQuantity(sku.sku_id, ($event.target as HTMLInputElement).value, sku.purchasable)"
                      />
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </section>

        <!-- 2. 收货地址（选取 1688 地址 / 手动输入地址） -->
        <section
          aria-label="收货地址"
          class="rounded-2xl border border-accent-200 bg-white p-4 shadow-sm dark:border-dark-700 dark:bg-dark-900/70"
        >
          <div class="flex flex-wrap items-center justify-between gap-2">
            <div class="flex items-center gap-2">
              <PhMapPin :size="18" class="text-primary-600 dark:text-primary-400" aria-hidden="true" />
              <h2 class="text-base font-semibold text-accent-950 dark:text-white">2. 收货地址</h2>
            </div>
            <div class="flex items-center gap-1.5 rounded-xl bg-accent-100 p-1 text-xs dark:bg-dark-800">
              <button
                type="button"
                class="rounded-lg px-2.5 py-1 font-medium transition"
                :class="addressMode === 'saved'
                  ? 'bg-white text-primary-700 shadow-sm dark:bg-dark-700 dark:text-primary-300'
                  : 'text-accent-600 hover:text-accent-950 dark:text-accent-300'"
                @click="addressMode = 'saved'"
              >
                选取 1688 地址
              </button>
              <button
                type="button"
                class="rounded-lg px-2.5 py-1 font-medium transition"
                :class="addressMode === 'manual'
                  ? 'bg-white text-primary-700 shadow-sm dark:bg-dark-700 dark:text-primary-300'
                  : 'text-accent-600 hover:text-accent-950 dark:text-accent-300'"
                @click="addressMode = 'manual'"
              >
                手动输入地址
              </button>
            </div>
          </div>

          <p v-if="addressNotice" class="mt-3 text-xs text-amber-700 dark:text-amber-300">{{ addressNotice }}</p>
          <p v-if="addressError" role="alert" class="mt-3 text-xs text-rose-700 dark:text-rose-300">{{ addressError }}</p>

          <!-- 模式 A：选取 1688 地址 -->
          <div v-if="addressMode === 'saved'" class="mt-3 space-y-3">
            <div class="flex items-center justify-between gap-2">
              <label class="text-xs font-medium text-accent-600 dark:text-accent-300" for="direct-purchase-saved-address">
                从当前 1688 账号保存的收货地址中选取
              </label>
              <button
                type="button"
                class="btn btn-outline !min-h-7 !rounded-lg !px-2.5 !py-1 text-xs"
                :disabled="!state?.authorized || addressLoading || actionBusy"
                @click="directStore.loadAddresses(true)"
              >
                {{ addressLoading ? '读取中…' : '刷新 1688 地址' }}
              </button>
            </div>

            <select
              id="direct-purchase-saved-address"
              v-model="selectedAddressId"
              aria-label="选择 1688 收货地址"
              class="input"
              :disabled="!canOperate || addressLoading || actionBusy"
            >
              <option value="" disabled>{{ savedAddresses.length ? '请选择 1688 收货地址' : '暂无可用 1688 地址，可刷新或切换手动输入' }}</option>
              <option
                v-for="item in savedAddresses"
                :key="item.id"
                :value="item.id"
                :disabled="Boolean(item.blocked_reason)"
              >
                {{ item.is_default ? '【默认】' : '' }}{{ item.text.replace(/\n/g, ' · ') }}{{ item.blocked_reason ? '（信息不完整）' : '' }}
              </option>
            </select>

            <div
              v-if="selectedSavedAddress"
              class="rounded-xl border border-accent-200 bg-accent-50/70 p-3 text-sm whitespace-pre-wrap text-accent-800 dark:border-dark-700 dark:bg-dark-800/60 dark:text-accent-200"
            >
              {{ selectedSavedAddress.text }}
            </div>
          </div>

          <!-- 模式 B：手动输入（含粘贴智能解析 + 直接编辑） -->
          <div v-else class="mt-3 space-y-3">
            <div class="space-y-2 rounded-xl border border-dashed border-accent-300 bg-accent-50/60 p-3 dark:border-dark-600 dark:bg-dark-800/40">
              <label class="block text-xs font-medium text-accent-600 dark:text-accent-300">
                粘贴整段地址自动识别（可选，也可直接在下方逐项填写）
                <textarea
                  v-model="rawAddressText"
                  aria-label="粘贴完整地址"
                  class="input mt-1 text-sm"
                  rows="2"
                  maxlength="4000"
                  placeholder="例如：张三 13800138000 广东省深圳市南山区科技路1号"
                  :disabled="addressParsing || actionBusy"
                />
              </label>
              <button
                type="button"
                class="btn btn-outline !min-h-8 !rounded-lg !px-3 !py-1 text-xs"
                :disabled="!rawAddressText.trim() || addressParsing || actionBusy"
                @click="directStore.handleParseAddress"
              >
                {{ addressParsing ? '解析中…' : '智能解析填充' }}
              </button>
              <p v-for="warning in addressWarnings" :key="warning" role="status" class="text-xs text-amber-700 dark:text-amber-300">
                {{ warning }}
              </p>
            </div>

            <div class="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <label
                v-for="[key, label, maxLen] in addressEntries"
                :key="key"
                class="block text-xs font-medium text-accent-700 dark:text-accent-300"
                :class="key === 'address' ? 'sm:col-span-2' : ''"
              >
                {{ label }}
                <input
                  v-model="manualFields[key]"
                  class="input mt-1 text-sm"
                  :aria-label="label"
                  :maxlength="maxLen"
                  :disabled="actionBusy"
                />
              </label>
            </div>

            <label class="flex items-center gap-2 text-sm text-accent-800 dark:text-accent-200">
              <input
                v-model="manualAddressConfirmed"
                type="checkbox"
                :disabled="!manualAddressComplete || actionBusy"
                aria-label="确认手动收货地址"
              />
              已核对收货人、联系电话与省市区详细地址（仅用于本次下单，不修改 1688 地址簿）
            </label>
          </div>

          <div class="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-accent-200 pt-4 dark:border-dark-700">
            <p class="text-xs text-accent-500 dark:text-accent-400">
              先预览确认商品金额与运费，再决定是否创建 1688 订单。
            </p>
            <button
              type="button"
              class="btn btn-primary !rounded-xl"
              :disabled="!canPreview"
              @click="directStore.handlePreview"
            >
              <PhShoppingCart :size="16" aria-hidden="true" />
              {{ actionBusy ? '处理中…' : '预览订单与运费' }}
            </button>
          </div>
        </section>

        <!-- 3. 当前预览 / 下单结果与付款 -->
        <section
          v-if="activeRecord"
          aria-label="直购预览与订单结果"
          class="space-y-3 rounded-2xl border border-accent-200 bg-white p-4 shadow-sm dark:border-dark-700 dark:bg-dark-900/70"
        >
          <div class="flex flex-wrap items-center justify-between gap-2">
            <h2 class="text-base font-semibold text-accent-950 dark:text-white">
              {{ activeRecord.state === 'preview' ? '3. 订单预览确认' : '3. 直购订单结果' }}
            </h2>
            <span class="rounded-full bg-accent-100 px-2.5 py-0.5 text-xs font-semibold text-accent-700 dark:bg-dark-800 dark:text-accent-200">
              {{ stateLabels[activeRecord.state] || activeRecord.state }}
            </span>
          </div>

          <div class="space-y-1.5 text-sm">
            <p class="font-medium text-accent-950 dark:text-white">{{ activeRecord.preview.title }}</p>
            <ul class="space-y-1 rounded-xl bg-accent-50/70 p-2.5 text-xs dark:bg-dark-800/60">
              <li
                v-for="item in activeRecord.preview.items"
                :key="`${item.sku_id}:${item.spec_id}`"
                class="flex items-center justify-between gap-2"
              >
                <span class="truncate text-accent-800 dark:text-accent-200">{{ item.specification }}</span>
                <span class="shrink-0 font-semibold tabular-nums text-accent-950 dark:text-white">× {{ item.quantity }}</span>
              </li>
            </ul>
            <p class="text-xs text-accent-600 dark:text-accent-300">
              收货人：{{ activeRecord.preview.recipient }} · {{ activeRecord.preview.phone }}
            </p>
            <p class="text-xs text-accent-600 dark:text-accent-300">
              收货地址：{{ activeRecord.preview.address }}
            </p>
          </div>

          <dl class="grid grid-cols-2 gap-1.5 rounded-xl border border-accent-200 p-3 text-sm dark:border-dark-700">
            <dt class="text-accent-600 dark:text-accent-300">商品合计（共 {{ activeRecord.preview.total_quantity }} 件）</dt>
            <dd class="text-right tabular-nums">{{ money(activeRecord.preview.goods_fen) }}</dd>
            <dt class="text-accent-600 dark:text-accent-300">运费</dt>
            <dd class="text-right tabular-nums">{{ money(activeRecord.preview.shipping_fen) }}</dd>
            <dt class="font-semibold text-accent-950 dark:text-white">应付合计</dt>
            <dd class="text-right font-semibold tabular-nums text-primary-700 dark:text-primary-300">
              {{ money(activeRecord.preview.total_fen) }}
            </dd>
          </dl>

          <!-- 待提交确认 -->
          <div v-if="activeRecord.state === 'preview' && !submissionUncertain" class="space-y-3 border-t border-accent-200 pt-3 dark:border-dark-700">
            <label class="block text-sm">
              预选支付渠道
              <select v-model="payChannel" aria-label="预选支付渠道" class="input mt-1" :disabled="actionBusy">
                <option value="" disabled>请选择支付渠道</option>
                <option v-for="ch in activeRecord.preview.pay_channels" :key="ch" :value="ch">
                  {{ channelLabels[ch] || ch }}
                </option>
              </select>
            </label>
            <label class="flex items-start gap-2 text-sm text-accent-800 dark:text-accent-200">
              <input v-model="createConfirmed" type="checkbox" :disabled="actionBusy || !payChannel" aria-label="确认创建直购订单" />
              <span>已核对以上规格、数量、收货地址与应付总额，确认通过万邦创建 1688 订单（创建后不自动扣款）。</span>
            </label>
            <button
              type="button"
              class="btn btn-primary !rounded-xl"
              :disabled="!canCreate"
              @click="directStore.handleCreate"
            >
              {{ actionBusy ? '正在下单…' : '确认创建 1688 订单' }}
            </button>
          </div>

          <!-- 已创建 / 待核验 / 已结束 -->
          <div v-else class="space-y-3 border-t border-accent-200 pt-3 dark:border-dark-700">
            <p v-if="activeRecord.message" role="status" class="text-sm text-accent-700 dark:text-accent-200">
              {{ activeRecord.message }}
            </p>
            <p v-if="submissionUncertain && activeRecord.state === 'preview'" role="status" class="text-sm text-amber-700 dark:text-amber-300">
              下单回执尚未确认，请点击「刷新状态」核对原单，勿重复下单。
            </p>
            <div v-if="activeRecord.order_numbers.length" class="space-y-1 text-sm">
              <p v-for="num in activeRecord.order_numbers" :key="num" class="break-all">
                1688 订单号：<strong class="font-mono">{{ num }}</strong>
              </p>
              <p v-if="activeRecord.order_status">当前状态：{{ activeRecord.order_status }}</p>
              <p v-if="activeRecord.quota_credited" class="text-xs font-medium text-primary-700 dark:text-primary-300">
                已按实付金额计入万邦调用额度
              </p>
            </div>

            <div class="flex flex-wrap items-center gap-2">
              <button
                v-if="['created', 'unknown', 'submitting', 'closed'].includes(activeRecord.state) || submissionUncertain"
                type="button"
                class="btn btn-outline !min-h-9 !rounded-xl text-sm"
                :disabled="actionBusy || recordBusyId === activeRecord.id"
                @click="directStore.handleReconcile(activeRecord)"
              >
                {{ recordBusyId === activeRecord.id ? '查询中…' : '刷新状态' }}
              </button>
              <button
                v-if="activeRecord.state === 'created' && !activeOrderPaid"
                type="button"
                class="btn btn-primary !min-h-9 !rounded-xl text-sm"
                :disabled="actionBusy || recordBusyId === activeRecord.id"
                @click="directStore.handleCashier(activeRecord)"
              >
                获取 1688 收银台链接
              </button>
              <a
                v-if="cashierUrls[activeRecord.id]"
                :href="cashierUrls[activeRecord.id]"
                target="_blank"
                rel="noopener noreferrer"
                class="btn btn-primary !min-h-9 !rounded-xl text-sm"
              >
                打开 1688 收银台 <PhArrowSquareOut :size="14" aria-hidden="true" />
              </a>
              <a
                v-if="['created', 'unknown', 'submitting', 'closed'].includes(activeRecord.state)"
                href="https://trade.1688.com/order/buyer_order_list.htm"
                target="_blank"
                rel="noopener noreferrer"
                class="btn btn-outline !min-h-9 !rounded-xl text-sm"
              >
                在 1688 查看订单 <PhArrowSquareOut :size="14" aria-hidden="true" />
              </a>
            </div>

            <!-- 可选：已签约免密支付 -->
            <div
              v-if="showProtocolPay"
              class="space-y-2 rounded-xl border border-accent-200 bg-accent-50/60 p-3 text-sm dark:border-dark-700 dark:bg-dark-800/40"
            >
              <div class="flex flex-wrap items-center justify-between gap-2">
                <span class="font-medium text-accent-900 dark:text-accent-100">
                  免密直接支付（可选 · {{ channelLabels[activeRecord.pay_channel] || activeRecord.pay_channel }}）
                </span>
                <button
                  type="button"
                  class="btn btn-outline !min-h-7 !rounded-lg !px-2.5 !py-1 text-xs"
                  :disabled="agreementLoading || actionBusy"
                  @click="directStore.handleCheckAgreement(Boolean(agreement))"
                >
                  {{ agreementLoading ? '检查中…' : agreement ? '刷新免密签约状态' : '检查免密签约状态' }}
                </button>
              </div>
              <template v-if="agreement">
                <p class="text-xs text-accent-700 dark:text-accent-300">{{ agreement.message }}</p>
                <a
                  v-if="!agreement.ready && agreement.sign_url"
                  :href="agreement.sign_url"
                  target="_blank"
                  rel="noopener noreferrer"
                  class="inline-flex items-center gap-1 text-xs font-medium text-primary-700 hover:underline dark:text-primary-300"
                >
                  前往 1688 签约免密 <PhArrowSquareOut :size="12" aria-hidden="true" />
                </a>
                <template v-if="agreement.ready">
                  <label class="flex items-center gap-2 text-xs text-accent-800 dark:text-accent-200">
                    <input v-model="payConfirmed" type="checkbox" :disabled="actionBusy" aria-label="确认免密支付" />
                    确认通过 {{ channelLabels[activeRecord.pay_channel] || activeRecord.pay_channel }} 免密直接扣款 {{ money(activeRecord.preview.total_fen) }}
                  </label>
                  <button
                    type="button"
                    class="btn btn-primary !min-h-8 !rounded-lg !px-3 !py-1 text-xs"
                    :disabled="!payConfirmed || actionBusy"
                    @click="directStore.handleProtocolPay"
                  >
                    确认免密支付
                  </button>
                </template>
              </template>
            </div>
          </div>
        </section>
      </div>

      <!-- 右侧：本人独立直购记录 -->
      <aside class="space-y-3 rounded-2xl border border-accent-200 bg-white p-4 shadow-sm dark:border-dark-700 dark:bg-dark-900/70">
        <div class="flex items-center justify-between gap-2">
          <div>
            <h2 class="text-base font-semibold text-accent-950 dark:text-white">直购记录</h2>
            <p class="text-xs text-accent-500 dark:text-accent-400">仅记录本页直购订单，付款后点「刷新状态」可同步额度。</p>
          </div>
          <span class="rounded-full bg-accent-100 px-2 py-0.5 text-xs font-semibold tabular-nums text-accent-700 dark:bg-dark-800 dark:text-accent-300">
            {{ state?.records.length || 0 }}
          </span>
        </div>

        <p v-if="!state?.records.length" class="rounded-xl bg-accent-50/70 p-4 text-center text-sm text-accent-500 dark:bg-dark-800/50 dark:text-accent-400">
          暂无直购记录
        </p>

        <div v-else class="space-y-3">
          <article
            v-for="row in state.records"
            :key="row.id"
            class="space-y-2 rounded-xl border border-accent-200 p-3 text-xs transition dark:border-dark-700"
            :class="activeRecord?.id === row.id ? 'border-primary-400 bg-primary-50/40 dark:border-primary-500/60 dark:bg-primary-500/10' : ''"
          >
            <div class="flex items-start justify-between gap-2">
              <div class="flex min-w-0 items-start gap-2">
                <img
                  v-if="directStore.displayImage(row.preview.main_image)"
                  :src="directStore.displayImage(row.preview.main_image)"
                  referrerpolicy="no-referrer"
                  loading="lazy"
                  alt=""
                  class="size-9 shrink-0 rounded-lg border border-accent-200 object-cover dark:border-dark-700"
                  @error="directStore.markImageBroken(row.preview.main_image)"
                />
                <p class="line-clamp-2 font-semibold text-accent-950 dark:text-white">{{ row.preview.title }}</p>
              </div>
              <span class="shrink-0 rounded-full bg-accent-100 px-2 py-0.5 text-[11px] font-medium text-accent-700 dark:bg-dark-800 dark:text-accent-200">
                {{ row.order_status || stateLabels[row.state] || row.state }}
              </span>
            </div>

            <div class="flex flex-wrap items-center justify-between gap-2 text-accent-600 dark:text-accent-300">
              <span>{{ row.preview.items.length }} 个规格 · 共 {{ row.preview.total_quantity }} 件</span>
              <span class="font-semibold tabular-nums text-accent-950 dark:text-white">{{ money(row.preview.total_fen) }}</span>
            </div>

            <p v-if="row.order_numbers.length" class="break-all font-mono text-[11px] text-accent-600 dark:text-accent-300">
              单号：{{ row.order_numbers.join('、') }}
            </p>
            <div class="flex flex-wrap items-center gap-2 text-[11px] text-accent-500 dark:text-accent-400">
              <span>{{ formatTime(row.created_at) }}</span>
              <span
                v-if="row.quota_credited"
                class="rounded-full bg-primary-100 px-1.5 py-0.5 font-medium text-primary-800 dark:bg-primary-500/20 dark:text-primary-200"
              >
                额度已入账
              </span>
            </div>

            <div class="flex flex-wrap items-center gap-1.5 pt-1">
              <button
                type="button"
                class="btn btn-outline !min-h-7 !rounded-lg !px-2 !py-1 text-[11px]"
                @click="directStore.inspectRecord(row)"
              >
                查看详情
              </button>
              <button
                v-if="['created', 'unknown', 'submitting', 'closed'].includes(row.state)"
                type="button"
                class="btn btn-outline !min-h-7 !rounded-lg !px-2 !py-1 text-[11px]"
                :disabled="actionBusy || recordBusyId === row.id"
                @click="directStore.handleReconcile(row)"
              >
                {{ recordBusyId === row.id ? '查询中…' : '刷新状态' }}
              </button>
              <button
                v-if="row.state === 'created' && !paidStatusCodes.has(row.order_status_code) && row.payment?.state !== 'succeeded' && row.payment?.state !== 'settled'"
                type="button"
                class="btn btn-outline !min-h-7 !rounded-lg !px-2 !py-1 text-[11px]"
                :disabled="actionBusy || recordBusyId === row.id"
                @click="directStore.handleCashier(row)"
              >
                收银台付款
              </button>
              <a
                v-if="cashierUrls[row.id]"
                :href="cashierUrls[row.id]"
                target="_blank"
                rel="noopener noreferrer"
                class="inline-flex items-center gap-0.5 font-medium text-primary-700 hover:underline dark:text-primary-300"
              >
                打开支付 <PhArrowSquareOut :size="11" aria-hidden="true" />
              </a>
            </div>
          </article>
        </div>
      </aside>
    </div>
  </div>
</template>
