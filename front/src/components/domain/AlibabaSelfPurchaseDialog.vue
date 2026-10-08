<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import WorkspaceDialog from '@/components/shared/WorkspaceDialog.vue'
import { selfPurchaseOptions, previewSelfPurchase, createSelfPurchase, reconcileSelfPurchase, selfPurchaseCashier } from '@/api/alibabaSelfPurchase'
import type { SelfPurchaseOptions, SelfPurchaseRecord } from '@/api/alibabaSelfPurchase'

const props = defineProps<{ orderId: string; lineKey: string; title: string }>()
const emit = defineEmits<{ close: []; updated: [] }>()
const options = ref<SelfPurchaseOptions | null>(null)
const candidateId = ref(''), quantity = ref(1)
const record = ref<SelfPurchaseRecord | null>(null)
const payChannel = ref(''), cashierUrl = ref(''), error = ref('')
const busy = ref(false), confirmed = ref(false), submissionUncertain = ref(false)
const labels: Record<string, string> = { preview: '待确认', expired: '预览已失效', submitting: '提交中／待核验', unknown: '创建结果待核验', created: '已创建', failed: '创建失败', closed: '已结束' }
const paymentLabels: Record<string, string> = { alipay: '支付宝', shegou: '先采后付（预选）' }
const pending = computed(() => options.value?.records.some(r => (['submitting', 'unknown'].includes(r.state) || (r.state === 'created' && !r.purchase_record_id))))
const canSubmit = computed(() => record.value?.state === 'preview' && !!options.value?.can_purchase && !submissionUncertain.value && confirmed.value && !!payChannel.value && !busy.value)
const money = (fen: number) => `¥${(fen / 100).toFixed(2)}`

watch([candidateId, quantity], () => {
  if (record.value?.state === 'preview' && !!options.value?.can_purchase && !submissionUncertain.value) record.value = null
  confirmed.value = false
})
watch(payChannel, () => { confirmed.value = false })

async function run(action: () => Promise<void>) {
  if (busy.value) return
  busy.value = true; error.value = ''; cashierUrl.value = ''
  try { await action() }
  catch (exc) { error.value = exc instanceof Error ? exc.message : '采购操作失败，请重试查询' }
  finally { busy.value = false }
}
async function load() {
  const firstLoad = !options.value
  options.value = await selfPurchaseOptions(props.orderId, props.lineKey)
  if (firstLoad) quantity.value = Math.max(1, options.value.remaining_quantity)
  if (!candidateId.value && options.value.candidates.length === 1) candidateId.value = options.value.candidates[0]!.id
  if (record.value) {
    record.value = options.value.records.find(r => r.id === record.value!.id) || record.value
  } else {
    record.value = options.value.records.find(r => ['submitting', 'unknown', 'created'].includes(r.state)) || null
  }
}
function chooseRecord(value: SelfPurchaseRecord) {
  record.value = value; payChannel.value = value.pay_channel; confirmed.value = false; cashierUrl.value = ''
}
async function preview() {
  await run(async () => {
    record.value = await previewSelfPurchase({ order_id: props.orderId, line_key: props.lineKey, candidate_id: candidateId.value, quantity: quantity.value })
    payChannel.value = ''; confirmed.value = false; submissionUncertain.value = false
    await load()
  })
}
async function create() {
  if (!canSubmit.value || !record.value) return
  const id = record.value.id
  await run(async () => {
    // HTTP 回执丢失也只允许查询原记录，不生成新提交键。
    submissionUncertain.value = true
    record.value = await createSelfPurchase(id, payChannel.value)
    emit('updated')
    submissionUncertain.value = false
    await load()
  })
}
async function reconcile() {
  if (!record.value) return
  const id = record.value.id
  await run(async () => {
    await load()
    const updated = record.value && ['created', 'unknown', 'submitting', 'closed'].includes(record.value.state)
      ? await reconcileSelfPurchase(id) : record.value
    submissionUncertain.value = false
    await load()
    record.value = updated
    emit('updated')
  })
}
async function cashier() {
  if (record.value) await run(async () => { cashierUrl.value = await selfPurchaseCashier(record.value!.id) })
}
onMounted(() => { void run(load) })
</script>

<template>
  <WorkspaceDialog :open="true" title="1688 采购" width="680px" :close-disabled="busy" @close="emit('close')">
    <div class="space-y-4">
      <p class="font-semibold">{{ title }}</p>
      <p class="text-sm text-accent-600 dark:text-accent-300">采购到该 1688 账号的默认收货地址。</p>
      <p v-if="error" role="alert" class="rounded-lg bg-rose-50 p-3 text-sm text-rose-700 dark:bg-rose-950 dark:text-rose-200">{{ error }}</p>
      <p v-if="busy" role="status">正在处理…</p>
      <template v-if="options">
        <div v-if="options.blocked_reason" role="alert" class="rounded-lg bg-amber-50 p-3 text-sm text-amber-800 dark:bg-amber-950 dark:text-amber-200">
          <p class="font-semibold">不能自动采购</p>
          <p class="mt-1">{{ options.blocked_reason }}</p>
        </div>
        <fieldset v-if="options.candidates.length" class="space-y-3" :disabled="busy || pending || submissionUncertain || !options.can_purchase">
          <label class="block text-sm">采购规格
            <select v-model="candidateId" class="input mt-1" aria-label="采购规格">
              <option value="" disabled>请选择</option>
              <option v-for="c in options.candidates" :key="c.id" :value="c.id">{{ c.specification }} · SKU {{ c.sku_id }}</option>
            </select>
          </label>
          <label class="block text-sm">采购数量（还需 {{ options.remaining_quantity }} 件）
            <input v-model.number="quantity" type="number" min="1" :max="options.remaining_quantity" step="1" class="input mt-1" aria-label="采购数量" />
          </label>
          <button class="btn btn-outline" :disabled="!candidateId || !Number.isInteger(quantity) || quantity < 1 || quantity > options.remaining_quantity" @click="preview">读取默认地址并预览</button>
        </fieldset>
        <section v-if="record" class="space-y-3 rounded-xl border border-accent-200 p-4 dark:border-dark-600" aria-label="采购结果">
          <div class="flex justify-between gap-3"><b>{{ labels[record.state] || record.state }}</b><span>{{ record.preview.quantity }} 件</span></div>
          <p class="text-sm">{{ record.preview.candidate.specification }}</p>
          <p class="text-sm">收货人：{{ record.preview.recipient }} · {{ record.preview.phone }}</p>
          <p class="text-sm">{{ record.preview.address }}</p>
          <dl class="grid grid-cols-2 gap-2 text-sm">
            <dt>商品金额</dt><dd class="text-right">{{ money(record.preview.goods_fen) }}</dd>
            <dt>运费</dt><dd class="text-right">{{ money(record.preview.shipping_fen) }}</dd>
            <dt class="font-semibold">预览合计</dt><dd class="text-right font-semibold">{{ money(record.preview.total_fen) }}</dd>
          </dl>
          <template v-if="record.state === 'preview' && !submissionUncertain">
            <label class="block text-sm">预选支付渠道
              <select v-model="payChannel" class="input mt-1" aria-label="预选支付渠道" :disabled="busy">
                <option value="" disabled>请选择</option>
                <option v-for="channel in record.preview.pay_channels" :key="channel" :value="channel">{{ paymentLabels[channel] || channel }}</option>
              </select>
            </label>
            <p class="text-sm text-accent-600 dark:text-accent-300">创建后需在 1688 收银台确认支付。选择先采后付不会在这里发起扣款，可用额度及还款日期以收银台为准。</p>
            <label class="flex gap-2 text-sm"><input v-model="confirmed" type="checkbox" :disabled="busy" class="mt-1" aria-label="确认真实采购" />已核对规格、数量、地址和金额，确认创建真实采购订单。</label>
            <button class="btn btn-primary" :disabled="!canSubmit" @click="create">确认创建订单</button>
          </template>
          <p v-if="record.message" role="status" class="text-sm">{{ record.message }}</p>
          <p v-if="submissionUncertain && record.state === 'preview'" role="status" class="text-sm">提交回执未确认，请查询原请求，勿重复下单。</p>
          <p v-for="number in record.order_numbers" :key="number" class="break-all text-sm">1688 订单号：<strong>{{ number }}</strong></p>
          <p v-if="record.purchase_record_id" class="text-sm text-green-700">已关联当前销售订单，可在订单详情查询采购状态和物流。</p>
          <p v-if="record.order_status" class="text-sm">订单状态：{{ record.order_status }}</p>
          <p v-if="record.pay_channel" class="text-sm">预选渠道：{{ paymentLabels[record.pay_channel] || record.pay_channel }}</p>
          <div class="flex flex-wrap gap-3">
            <button v-if="['created','unknown','submitting','closed'].includes(record.state) || submissionUncertain" class="btn btn-outline" :disabled="busy" @click="reconcile">查询原订单</button>
            <button v-if="record.state === 'created'" class="btn btn-outline" :disabled="busy" @click="cashier">获取 1688 收银台链接</button>
            <a v-if="cashierUrl" :href="cashierUrl" class="btn btn-primary" target="_blank" rel="noopener noreferrer">打开 1688 收银台</a>
            <a v-if="['created', 'unknown', 'submitting', 'closed'].includes(record.state)" href="https://trade.1688.com/order/buyer_order_list.htm" class="btn btn-outline" target="_blank" rel="noopener noreferrer">在 1688 查看或取消</a>
          </div>
          <p class="break-all text-xs text-accent-500">采购请求号：{{ record.id }}</p>
        </section>
        <details v-if="options.records.length" class="text-sm">
          <summary class="cursor-pointer">采购记录（{{ options.records.length }}）</summary>
          <button v-for="r in options.records" :key="r.id" class="mt-2 block w-full rounded-lg border border-accent-200 p-3 text-left dark:border-dark-600" :disabled="busy" @click="chooseRecord(r)">
            {{ r.created_at }} · {{ labels[r.state] || r.state }} · {{ money(r.preview.total_fen) }}
            <span v-if="r.order_numbers.length" class="block break-all">{{ r.order_numbers.join('、') }}</span>
          </button>
        </details>
      </template>
      <button v-if="!busy" class="text-sm text-primary-700 dark:text-primary-300" @click="run(load)">刷新采购记录</button>
    </div>
  </WorkspaceDialog>
</template>
