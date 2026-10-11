<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import WorkspaceDialog from '@/components/shared/WorkspaceDialog.vue'
import AlibabaPaymentAgreement from './AlibabaPaymentAgreement.vue'
import AlibabaPurchasePayment from './AlibabaPurchasePayment.vue'
import { batchPurchaseOptions, previewBatchPurchase, createSelfPurchase, reconcileSelfPurchase, selfPurchaseCashier, paySelfPurchase } from '@/api/alibabaSelfPurchase'
import type { BatchPurchaseOptions, SelfPurchaseRecord } from '@/api/alibabaSelfPurchase'

const props = defineProps<{ orderId: string }>()
const emit = defineEmits<{ close: []; updated: [] }>()
const data = ref<BatchPurchaseOptions | null>(null)
const selected = ref<string[]>([])
const quantities = reactive<Record<string, number>>({})
const channels = reactive<Record<string, string>>({})
const cashier = reactive<Record<string, string>>({})
const uncertain = reactive(new Set<string>())
const records = ref<SelfPurchaseRecord[]>([])
const busy = ref(false), error = ref(''), confirmed = ref(false)
const paymentReady = reactive<Record<string, boolean>>({})
const money = (value: number) => `¥${(value / 100).toFixed(2)}`
const labels: Record<string, string> = { preview: '待提交', submitting: '提交中／待核验', unknown: '结果待核验', created: '已创建', failed: '创建失败', closed: '已结束' }
const paymentLabels: Record<string, string> = { alipay: '支付宝', shegou: '先采后付' }
const warehouse = computed(() => data.value?.lines.find(line => line.options.receiving_address)?.options)
const pendingLines = computed(() => new Set(records.value.filter(record => uncertain.has(record.id) || ['submitting', 'unknown'].includes(record.state) || (record.state === 'created' && !record.purchase_record_id)).flatMap(record => record.preview.items?.map(item => item.line_key) || [])))
const eligible = computed(() => data.value?.lines.filter(line => line.options.can_purchase && !pendingLines.value.has(line.line_key) && !line.options.records.some(record => ['submitting', 'unknown'].includes(record.state) || (record.state === 'created' && !record.purchase_record_id))) || [])
const drafts = computed(() => records.value.filter(record => record.state === 'preview' && !uncertain.has(record.id)))
const total = computed(() => drafts.value.reduce((sum, record) => sum + record.preview.total_fen, 0))
const validSelection = computed(() => selected.value.length > 0 && selected.value.length <= 50 && selected.value.every(key => {
  const line = eligible.value.find(row => row.line_key === key)
  const quantity = quantities[key] || 0
  return line && Number.isInteger(quantity) && quantity > 0 && quantity <= line.options.remaining_quantity
}))
const usedChannels = computed(() => [...new Set(drafts.value.map(record => channels[record.id]).filter((channel): channel is string => !!channel))].sort())
const alipayTotal = computed(() => drafts.value.filter(record => channels[record.id] === 'alipay').reduce((sum, record) => sum + record.preview.total_fen, 0))
const canSubmit = computed(() => !busy.value && confirmed.value && drafts.value.length > 0 && usedChannels.value.every(channel => paymentReady[channel]) && drafts.value.every(record => record.preview.pay_channels.includes(channels[record.id] || '')))
watch(usedChannels, channels => { for (const channel of Object.keys(paymentReady)) if (!channels.includes(channel)) delete paymentReady[channel] })

function discardPreviews() {
  records.value = records.value.filter(record => record.state !== 'preview' || uncertain.has(record.id))
  confirmed.value = false
}
watch([selected, quantities], discardPreviews, { deep: true })
watch(channels, () => { confirmed.value = false }, { deep: true })
async function load() {
  const fresh = await batchPurchaseOptions(props.orderId)
  const ids = new Set(fresh.records.map(record => record.id))
  records.value = [...records.value.filter(record => !ids.has(record.id) && (record.state === 'preview' || uncertain.has(record.id))), ...fresh.records]
  fresh.records.forEach(record => uncertain.delete(record.id))
  data.value = fresh
  fresh.lines.forEach(line => { quantities[line.line_key] ??= Math.max(1, line.options.remaining_quantity) })
}
async function run(action: () => Promise<void>) {
  if (busy.value) return
  busy.value = true; error.value = ''
  try { await action() }
  catch (exc) { error.value = exc instanceof Error ? exc.message : '采购操作失败，请刷新后核对' }
  finally { busy.value = false }
}
async function preview() {
  await run(async () => {
    const items = selected.value.map(key => {
      const line = eligible.value.find(row => row.line_key === key)!
      return { line_key: key, candidate_id: line.options.candidates[0]!.id, quantity: quantities[key]! }
    })
    const groups = await previewBatchPurchase({ order_id: props.orderId, receiving_revision: warehouse.value!.receiving_revision, items })
    discardPreviews()
    records.value = [...groups, ...records.value]
    groups.forEach(record => { channels[record.id] = record.preview.pay_channels[0] || '' })
  })
}
function replace(record: SelfPurchaseRecord) {
  records.value = records.value.map(old => old.id === record.id ? record : old)
}
async function submit() {
  if (!canSubmit.value) return
  await run(async () => {
    const pending = [...drafts.value]
    confirmed.value = false
    for (const record of pending) {
      uncertain.add(record.id)
      try {
        const updated = await createSelfPurchase(record.id, channels[record.id]!)
        replace(updated)
        uncertain.delete(record.id)
        emit('updated')
        if (updated.state === 'created' && !['succeeded','settled'].includes(updated.payment?.state || '')) {
          error.value = '本组支付尚未确认，已暂停后续分组，请核对支付结果。'
          break
        }
      } catch (exc) {
        error.value = `${exc instanceof Error ? exc.message : '未收到提交回执'}；已暂停后续分组，请查询原请求。`
        break
      }
    }
    await load()
  })
}
async function reconcile(record: SelfPurchaseRecord) {
  await run(async () => {
    replace(await reconcileSelfPurchase(record.id))
    uncertain.delete(record.id)
    emit('updated')
    await load()
  })
}
async function pay(record: SelfPurchaseRecord) {
  await run(async () => {
    uncertain.add(record.id)
    replace(await paySelfPurchase(record.id, record.preview.total_fen))
    uncertain.delete(record.id)
    emit('updated')
  })
}
onMounted(() => run(load))
</script>

<template>
  <WorkspaceDialog :open="true" title="1688 批量采购" width="880px" :close-disabled="busy" @close="emit('close')">
    <div class="space-y-5">
      <p v-if="error" role="alert" class="text-sm text-red-600">{{ error }}</p>
      <p v-if="busy" role="status" class="text-sm">正在处理，请稍候…</p>
      <section v-if="warehouse?.receiving_address" class="rounded-lg bg-accent-50 p-3 text-sm dark:bg-dark-800" aria-label="统一收货仓">
        <b>{{ warehouse.warehouse_name }}</b>
        <p>{{ warehouse.receiving_address.fullName }} · {{ warehouse.receiving_address.mobile || warehouse.receiving_address.phone }}</p>
        <p>{{ [warehouse.receiving_address.provinceText, warehouse.receiving_address.cityText, warehouse.receiving_address.areaText, warehouse.receiving_address.townText, warehouse.receiving_address.address].filter(Boolean).join(' ') }}</p>
      </section>
      <fieldset v-if="data" :disabled="busy" class="space-y-3">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <h4 class="font-semibold">选择采购商品</h4>
          <button class="btn btn-outline" :disabled="!eligible.length || eligible.length > 50" @click="selected = eligible.map(line => line.line_key)">全选可采购商品</button>
        </div>
        <p v-if="data.lines.length > 50" class="text-sm">每次最多选择 50 个 SKU，请分批采购。</p>
        <div v-for="line in data.lines" :key="line.line_key" class="flex items-start gap-3 rounded-lg border border-accent-200 p-3 dark:border-dark-600">
          <input v-model="selected" :value="line.line_key" type="checkbox" :aria-label="`采购 ${line.sku}`" :disabled="!eligible.includes(line)" class="mt-1" />
          <div class="min-w-0 flex-1 text-sm">
            <b>{{ line.title }}</b><p>{{ line.sku }} · {{ line.options.candidates[0]?.specification }}</p>
            <p>{{ line.supplier || '供应商以预览分组为准' }}</p>
            <p v-if="line.options.blocked_reason" class="text-amber-700">{{ line.options.blocked_reason }}</p>
            <p v-else-if="!eligible.includes(line)" class="text-amber-700">有待核验采购，请先查询原订单。</p>
          </div>
          <label class="text-sm">数量
            <input v-model.number="quantities[line.line_key]" type="number" min="1" :max="line.options.remaining_quantity" step="1" class="input w-20" :aria-label="`${line.sku} 采购数量`" :disabled="!eligible.includes(line)" />
            <span class="block text-xs">还需 {{ line.options.remaining_quantity }} 件</span>
          </label>
        </div>
        <button class="btn btn-outline" :disabled="!validSelection || !warehouse?.receiving_revision" @click="preview">按供应商预览</button>
      </fieldset>
      <section v-for="record in records" :key="record.id" class="space-y-3 rounded-lg border border-accent-200 p-4 dark:border-dark-600" :aria-label="`采购分组 ${record.preview.supplier}`">
        <div class="flex justify-between gap-3"><b>{{ record.preview.supplier }}</b><span>{{ uncertain.has(record.id) ? '回执待核验' : labels[record.state] || record.state }}</span></div>
        <ul class="space-y-1 text-sm"><li v-for="item in record.preview.items" :key="item.line_key">{{ item.title }} · {{ item.candidate.specification }} × {{ item.quantity }}</li></ul>
        <p class="text-sm">商品 {{ money(record.preview.goods_fen) }} + 运费 {{ money(record.preview.shipping_fen) }} = <b>{{ money(record.preview.total_fen) }}</b></p>
        <p class="text-xs">收货：{{ record.preview.recipient }} · {{ record.preview.address }}</p>
        <p v-if="record.message && !record.payment" role="status" class="text-sm">{{ record.message }}</p>
        <p v-for="number in record.order_numbers" :key="number" class="text-sm">采购单号：{{ number }}</p>
        <p v-if="record.purchase_record_id" class="text-sm text-green-700">已回填对应 SKU；发货后可刷新采购进度获取运单。</p>
        <AlibabaPurchasePayment :record="record" :disabled="busy || uncertain.has(record.id)" @pay="pay(record)" />
        <label v-if="record.state === 'preview' && !uncertain.has(record.id)" class="block text-sm">支付渠道
          <select v-model="channels[record.id]" :disabled="busy" class="input" :aria-label="`${record.preview.supplier} 支付渠道`"><option v-for="channel in record.preview.pay_channels" :key="channel" :value="channel">{{ paymentLabels[channel] || channel }}</option></select>
        </label>
        <div class="flex flex-wrap gap-2">
          <button v-if="['submitting','unknown','created','closed'].includes(record.state) || uncertain.has(record.id)" class="btn btn-outline" :disabled="busy" @click="reconcile(record)">查询原订单</button>
          <button v-if="record.state === 'created' && !['submitting','unknown','succeeded','settled'].includes(record.payment?.state || '')" class="btn btn-outline" :disabled="busy || uncertain.has(record.id)" @click="run(async () => { cashier[record.id] = await selfPurchaseCashier(record.id) })">获取收银台链接</button>
          <a v-if="cashier[record.id]" :href="cashier[record.id]" target="_blank" rel="noopener noreferrer" class="btn btn-outline">打开 1688 收银台</a>
        </div>
      </section>
      <section v-if="drafts.length" class="space-y-3" aria-label="批量提交确认">
        <p>本次 {{ drafts.length }} 个采购分组，预览合计 <b>{{ money(total) }}</b>。</p>
        <AlibabaPaymentAgreement v-for="channel in usedChannels" :key="channel" :channel="channel" :disabled="busy" @ready="paymentReady[channel] = $event" />
        <p v-if="alipayTotal" class="text-sm">支付宝免密将直接扣款 {{ money(alipayTotal) }}。</p>
        <p class="text-sm">分组依次创建采购订单并按所选渠道免密支付：支付宝直接扣款；先采后付使用额度。支付结果待确认时暂停后续分组。</p>
        <label class="flex gap-2 text-sm"><input v-model="confirmed" type="checkbox" :disabled="busy" aria-label="确认批量采购" />已核对商品、数量、收货仓及金额，确认创建采购订单并按各组所选渠道免密支付。</label>
        <button class="btn btn-primary" :disabled="!canSubmit" @click="submit">确认批量创建并支付</button>
      </section>
      <button class="text-sm text-primary-700 dark:text-primary-300" :disabled="busy" @click="run(load)">刷新采购记录</button>
    </div>
  </WorkspaceDialog>
</template>
