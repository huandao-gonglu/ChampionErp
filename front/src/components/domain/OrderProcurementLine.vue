<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { procurementCommand } from '@/api/orders'
import type { OrderDetail, OrderSnapshot, ProcurementLine, ProcurementSource } from '@/types/orders'
import OrderAmountDetails from './OrderAmountDetails.vue'
const props = defineProps<{ item: ProcurementLine; order: OrderSnapshot }>()
const emit = defineEmits<{ updated: [detail: OrderDetail] }>()
const error = ref('')
const busy = ref(false)
const selection = computed(() => props.item.selection)
const source = reactive<ProcurementSource>({
  supplier: '',
  source_platform: '',
  product_url: '',
  source_sku_id: '',
  specification: '',
  sku_url: '',
  sku_url_verified: false,
  ...props.item.selection.source,
})
const quantity = ref(Math.max(1, props.item.remaining_quantity))
const purchaseNumber = ref('')
const editable = computed(
  () =>
    !['cancelled', 'delivered'].includes(props.order.state) &&
    !!selection.value.line_key &&
    !selection.value.reason.includes('缺少唯一身份')
)
let lastPurchase = { fingerprint: '', id: '' }
async function execute(
  action: 'select-source' | 'record-purchase' | 'cancel-purchase',
  body: Record<string, unknown>
) {
  if (busy.value) return
  busy.value = true
  error.value = ''
  try {
    const result = await procurementCommand(action, { order_id: props.order.id, ...body })
    emit('updated', result)
    if (action === 'record-purchase') {
      purchaseNumber.value = ''
      lastPurchase = { fingerprint: '', id: '' }
    }
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : '采购操作失败，请刷新后重试'
  } finally {
    busy.value = false
  }
}
function select(candidateId = '') {
  return execute('select-source', {
    line_key: selection.value.line_key,
    revision: selection.value.revision,
    ...(candidateId ? { candidate_id: candidateId } : { source: { ...source } }),
  })
}
function recordPurchase() {
  const body = {
    line_key: selection.value.line_key,
    revision: selection.value.revision,
    quantity: quantity.value,
    purchase_order_number: purchaseNumber.value.trim(),
  }
  const fingerprint = JSON.stringify(body)
  if (lastPurchase.fingerprint !== fingerprint)
    lastPurchase = { fingerprint, id: crypto.randomUUID() }
  return execute('record-purchase', { ...body, request_id: lastPurchase.id })
}
async function copySpecification() {
  try {
    await navigator.clipboard.writeText(
      selection.value.source?.specification || props.item.line.title
    )
  } catch {
    error.value = '复制失败，请手动复制规格文字'
  }
}
</script>
<template>
  <article
    class="rounded-lg border border-accent-200 bg-white p-5 dark:border-dark-700 dark:bg-dark-900"
  >
    <div class="flex flex-wrap justify-between gap-3">
      <div>
        <h3 class="font-semibold">{{ item.line.title || '暂无商品标题' }}</h3>
        <p class="muted mt-1">
          销售 SKU：{{ item.line.sku || '未提供' }} · 数量 {{ item.line.quantity }}
        </p>
      </div>
      <span class="badge-info">已采购 {{ item.purchased_quantity }} / {{ item.line.quantity }}</span>
    </div>
    <OrderAmountDetails :value="item.line" :platform="order.platform" />
    <p class="muted">该 SKU 全部数量的小计</p>
    <p v-if="error" role="alert" class="mt-3 text-red-600">{{ error }}</p>
    <section class="mt-5 rounded-lg bg-accent-50 p-4 dark:bg-dark-950" aria-label="采购来源">
      <h4 class="font-semibold">
        采购来源 ·
        {{
          {
            unmatched: '待关联',
            matched: '已匹配，待确认',
            ambiguous: '存在多个候选',
            confirmed: '已确认',
          }[selection.status]
        }}
      </h4>
      <p v-if="selection.reason" class="muted mt-2">{{ selection.reason }}</p>
      <template v-if="selection.source">
        <p class="mt-2">
          {{ selection.source.supplier || selection.source.source_platform || '采购商品' }}
        </p>
        <p>采购规格：{{ selection.source.specification }}</p>
        <p class="muted">来源 SKU：{{ selection.source.source_sku_id || '未提供，请核对规格' }}</p>
        <div class="mt-3 flex flex-wrap gap-2">
          <a
            class="btn btn-primary"
            :href="
              selection.source.sku_url_verified
                ? selection.source.sku_url
                : selection.source.product_url
            "
            target="_blank"
            rel="noopener noreferrer"
          >{{
            selection.source.sku_url_verified ? '直达采购规格（人工确认）' : '打开采购商品'
          }}</a>
          <button class="btn btn-outline" @click="copySpecification">复制采购规格</button>
        </div>
        <p v-if="!selection.source.sku_url_verified" class="muted mt-2">
          此链接仅打开商品页，请按上述规格核对；系统不会假定已自动选中 SKU。
        </p>
      </template>
      <div v-if="selection.status !== 'confirmed' && editable" class="mt-3 space-y-3">
        <div
          v-for="candidate in selection.candidates"
          :key="candidate.id"
          class="rounded border border-accent-200 p-3 dark:border-dark-700"
        >
          <p>
            {{ candidate.source.specification }} · 来源 SKU
            {{ candidate.source.source_sku_id || '未提供' }}
          </p>
          <p class="muted break-all">{{ candidate.source.product_url }}</p>
          <p class="muted">
            内部 SKU {{ candidate.sku_id }} · 发布记录 {{ candidate.publication_id }}
          </p>
          <button class="btn btn-outline mt-2" :disabled="busy" @click="select(candidate.id)">
            确认此采购来源
          </button>
        </div>
      </div>
      <details v-if="editable" class="mt-4">
        <summary class="cursor-pointer font-semibold">
          {{ selection.status === 'confirmed' ? '更换来源或补充规格直达链接' : '人工关联采购来源' }}
        </summary>
        <form class="mt-3 grid gap-3 sm:grid-cols-2" @submit.prevent="select()">
          <label>采购平台<input
            v-model="source.source_platform"
            class="input mt-1 w-full"
            placeholder="例如 1688"
            maxlength="100"
          /></label>
          <label>供应商<input v-model="source.supplier" class="input mt-1 w-full" maxlength="200" /></label>
          <label class="sm:col-span-2">采购商品链接<input
            v-model="source.product_url"
            class="input mt-1 w-full"
            type="url"
            required
            maxlength="2000"
          /></label>
          <label>来源 SKU 编号<input
            v-model="source.source_sku_id"
            class="input mt-1 w-full"
            maxlength="200"
          /></label>
          <label>准确采购规格<input
            v-model="source.specification"
            class="input mt-1 w-full"
            required
            placeholder="颜色、尺寸、款式等"
            maxlength="1000"
          /></label>
          <label class="sm:col-span-2">规格直达链接（可选）<input
            v-model="source.sku_url"
            class="input mt-1 w-full"
            type="url"
            maxlength="2000"
          /></label>
          <label class="sm:col-span-2"><input v-model="source.sku_url_verified" type="checkbox" />
            我已打开链接，确认会选中上述来源 SKU</label>
          <p class="muted sm:col-span-2">
            更换来源只影响后续采购，已有采购记录保留原供应商和规格。
          </p>
          <button class="btn btn-primary justify-self-start" :disabled="busy">确认采购来源</button>
        </form>
      </details>
    </section>
    <section class="mt-5" aria-label="实际采购记录">
      <h4 class="font-semibold">实际采购记录</h4>
      <form
        v-if="
          selection.status === 'confirmed' &&
            order.state === 'pending_shipment' &&
            item.remaining_quantity > 0
        "
        class="mt-3 flex flex-wrap items-end gap-3"
        @submit.prevent="recordPurchase"
      >
        <label>已采购数量<input
          v-model.number="quantity"
          class="input mt-1 block w-28"
          type="number"
          min="1"
          :max="item.remaining_quantity"
          required
        /></label>
        <label>采购单号<input
          v-model="purchaseNumber"
          class="input mt-1 block"
          required
          maxlength="200"
        /></label>
        <button class="btn btn-primary" :disabled="busy">记录已采购</button>
      </form>
      <p class="muted mt-2">
        请在采购平台完成下单后记录。作废仅撤销 ERP 记录，不会取消采购平台订单。
      </p>
      <ul class="mt-3 space-y-3">
        <li
          v-for="record in item.records"
          :key="record.id"
          class="border-t border-accent-200 pt-3 dark:border-dark-700"
        >
          <p>
            {{ record.purchase_order_number }} · {{ record.quantity }} 件 ·
            {{ record.status === 'cancelled' ? '已作废' : '已采购' }}
          </p>
          <p class="muted">
            {{ record.source.supplier || record.source.source_platform }} ·
            {{ record.source.specification }} · 来源 SKU {{ record.source.source_sku_id }}
          </p>
          <a
            :href="record.source.product_url"
            target="_blank"
            rel="noopener noreferrer"
            class="text-cyan-700"
          >查看当时采购商品</a>
          <p class="muted">{{ new Date(record.created_at).toLocaleString('zh-CN') }}</p>
          <button
            v-if="record.status !== 'cancelled'"
            class="btn btn-outline mt-2"
            :disabled="busy"
            @click="execute('cancel-purchase', { record_id: record.id })"
          >
            作废本条记录
          </button>
        </li>
      </ul>
      <p v-if="!item.records.length" class="muted mt-3">尚无采购记录。</p>
    </section>
  </article>
</template>
