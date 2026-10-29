<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import WorkspaceDialog from '@/components/shared/WorkspaceDialog.vue'
import { onlineAction, type OnlineJob } from '@/api/onlineProducts'

const props = defineProps<{ job: OnlineJob; disabled?: boolean }>()
const emit = defineEmits<{ updated: [job: OnlineJob] }>()
const open = ref(false), busy = ref(false), acknowledged = ref(false), error = ref('')
const reviewed = ref(props.job)
const reviewTime = ref(Date.now())
let disposed = false
onBeforeUnmount(() => { disposed = true })
watch(() => props.job, job => {
  if (job.updated_at !== reviewed.value.updated_at || job.id !== reviewed.value.id) acknowledged.value = false
  reviewed.value = job
})
const eligible = computed(() => ['outcome_unknown', 'waiting_confirmation'].includes(reviewed.value.status))
const evidence = computed(() => reviewed.value.result.evidence)
const resultError = computed(() => {
  if (!eligible.value) return ''
  const result = reviewed.value.result
  if (result.read_errors?.length) return result.read_errors.join('；')
  return evidence.value?.checked_at ? '' : result.error || ''
})
const canClose = computed(() => {
  const result = reviewed.value.result
  const age = reviewTime.value - Date.parse(evidence.value?.checked_at || '')
  const checks = Object.values(result.confirmation || {})
  const observed = Object.values(evidence.value?.observed || {})
  return eligible.value && age >= 0 && age <= 300_000 && !result.platform_confirmation?.pending
    && !result.read_errors?.length && checks.length > 0 && checks.some(value => !value)
    && observed.length > 0 && observed.every(value => value !== null)
})
const names: Record<string, string> = { quantity: '库存', amount: '价格', currency: '币种', state: '销售状态', title: '标题', description: '描述', pictures: '图片', attributes: '属性', guarantee_period: '保修期限' }
const targets = computed(() => reviewed.value.result.changes || reviewed.value.request.changes as Record<string, unknown> || {})
function display(value: unknown): string {
  if (value == null) return '未取得'
  return typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value)
}
function begin() {
  reviewed.value = props.job
  reviewTime.value = Date.now()
  acknowledged.value = false
  error.value = ''
  open.value = true
}
async function query() {
  if (busy.value) return
  busy.value = true; acknowledged.value = false; error.value = ''
  reviewed.value = { ...reviewed.value, result: { ...reviewed.value.result, evidence: undefined } }
  try {
    const job = await onlineAction('reconcile', { job_id: reviewed.value.id })
    if (disposed) return
    reviewed.value = job
    reviewTime.value = Date.now()
    emit('updated', reviewed.value)
  } catch (e) { error.value = e instanceof Error ? e.message : '查询失败，请重新查询平台结果' }
  finally { busy.value = false }
}
async function closeOperation() {
  reviewTime.value = Date.now()
  if (busy.value || !canClose.value || !acknowledged.value) return
  busy.value = true; error.value = ''
  try {
    const job = await onlineAction('close', { job_id: reviewed.value.id, expected_updated_at: reviewed.value.updated_at, acknowledge: true })
    if (disposed) return
    reviewed.value = job
    emit('updated', job)
    open.value = false
  } catch (e) {
    acknowledged.value = false
    reviewed.value = { ...reviewed.value, result: { ...reviewed.value.result, evidence: undefined } }
    error.value = e instanceof Error ? e.message : '结束操作失败，请重新核对记录'
  } finally { busy.value = false }
}
</script>

<template>
  <button v-if="eligible" class="btn btn-outline" :disabled="disabled || busy" data-testid="review-unconfirmed" @click="begin">核对并结束操作</button>
  <WorkspaceDialog :open="open" title="核对未确认的操作" width="680px" :close-disabled="busy" @close="open=false">
    <div class="space-y-4">
      <p class="font-medium">{{ reviewed.result.before?.seller_sku || reviewed.target_id }} · {{ reviewed.result.before?.title }}</p>
      <p class="text-sm">结束后，该商品可以提交新的修改。原操作会保留为“已结束，结果未确认”；此操作不会撤销平台请求，也不会自动重新提交。</p>
      <p v-if="error" role="alert" class="text-sm text-rose-700 dark:text-rose-300">{{ error }}</p>
      <p v-else-if="resultError && !busy" role="alert" class="text-sm text-rose-700 dark:text-rose-300">{{ resultError }}</p>
      <button class="btn btn-outline" :disabled="busy || disabled || !eligible" @click="query">{{ busy ? '处理中…' : '查询平台结果' }}</button>
      <p v-if="reviewed.status === 'confirmed'" role="status" class="text-sm text-primary-700 dark:text-primary-300">平台已确认原修改生效，无需人工结束。</p>
      <template v-else>
        <p v-if="evidence?.checked_at" class="text-sm">最近核对：{{ new Date(evidence.checked_at).toLocaleString('zh-CN', {hour12:false}) }}<span v-if="reviewed.request.scope_id"> · 范围 {{ reviewed.request.scope_id }}</span></p>
        <table class="w-full text-left text-sm"><thead><tr><th>修改项</th><th>原目标值</th><th>平台当前值</th></tr></thead><tbody><tr v-for="(target, field) in targets" :key="field"><td>{{ names[field] || field }}</td><td><pre class="max-h-40 overflow-auto whitespace-pre-wrap break-words">{{ display(target) }}</pre></td><td><pre class="max-h-40 overflow-auto whitespace-pre-wrap break-words">{{ display(evidence?.observed?.[field]) }}</pre></td></tr></tbody></table>
        <p v-if="reviewed.result.platform_confirmation?.pending" role="status" class="text-sm text-amber-700 dark:text-amber-300">平台仍在处理原请求，暂不能结束。</p>
        <p v-else-if="!canClose" class="text-sm text-amber-700 dark:text-amber-300">请成功查询平台当前值，并在五分钟内核对。查询失败或记录变化时需重新查询。</p>
        <label class="flex items-start gap-2 text-sm"><input v-model="acknowledged" type="checkbox" :disabled="busy || !canClose" data-testid="acknowledge-unconfirmed" /><span>我已核对当前值，确认结束本地操作并允许新的修改；我了解原请求结果仍未确认，且可能延迟生效。</span></label>
      </template>
    </div>
    <template #footer><button class="btn btn-primary" :disabled="busy || disabled || !canClose || !acknowledged" data-testid="close-unconfirmed" @click="closeOperation">确认结束本地操作</button></template>
  </WorkspaceDialog>
</template>
