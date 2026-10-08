<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { selfPurchaseAddresses, parsePurchaseAddress } from '@/api/alibabaSelfPurchase'
import type { PurchaseAddressFields, PurchaseAddressSelection } from '@/api/alibabaSelfPurchase'
import type { BackendAlibabaPurchaseAddressCandidate } from '@/types/workflow.generated'

const props = defineProps<{ orderId: string; lineKey: string; disabled: boolean }>()
const emit = defineEmits<{ change: [value: PurchaseAddressSelection | null] }>()
const items = ref<BackendAlibabaPurchaseAddressCandidate[]>([])
const selectedId = ref(''), raw = ref(''), notice = ref(''), error = ref('')
const loading = ref(false), parsed = ref(false), confirmed = ref(false)
const warnings = ref<string[]>([])
const fields = reactive<PurchaseAddressFields>({ fullName: '', mobile: '', phone: '', provinceText: '', cityText: '', areaText: '', townText: '', address: '', postCode: '' })
const entries: Array<[keyof PurchaseAddressFields, string]> = [
  ['fullName', '收货人'], ['mobile', '手机号'], ['phone', '固定电话（选填）'],
  ['provinceText', '省'], ['cityText', '市'], ['areaText', '区／县'],
  ['townText', '街道／乡镇（选填）'], ['address', '详细地址'], ['postCode', '邮编（选填）'],
]
const selected = computed(() => items.value.find(item => item.id === selectedId.value))
const saved = computed(() => selected.value?.kind === '1688')
const editable = computed(() => selectedId.value === 'manual' || (!!selected.value && !saved.value))
const complete = computed(() => ['fullName', 'provinceText', 'cityText', 'areaText', 'address'].every(key => fields[key as keyof PurchaseAddressFields].trim()) && !!(fields.mobile.trim() || fields.phone.trim()))
const choice = computed<PurchaseAddressSelection | null>(() => {
  if (saved.value && !selected.value?.blocked_reason) return { address_id: selectedId.value }
  if (editable.value && parsed.value && confirmed.value && complete.value) return { address: { ...fields }, address_confirmed: true }
  return null
})
watch(choice, value => emit('change', value))
watch(fields, () => { confirmed.value = false })
watch(raw, () => { parsed.value = false; confirmed.value = false; warnings.value = [] })
function choose() {
  raw.value = selected.value?.text || ''
  parsed.value = false; confirmed.value = false; warnings.value = []; error.value = ''
}
async function run(action: () => Promise<void>) {
  if (loading.value) return
  loading.value = true; error.value = ''
  try { await action() }
  catch (exc) { error.value = exc instanceof Error ? exc.message : '地址读取失败，请重试' }
  finally { loading.value = false }
}
async function load() {
  await run(async () => {
    const response = await selfPurchaseAddresses(props.orderId, props.lineKey)
    items.value = response.items; notice.value = response.notice
    if (!selectedId.value) {
      const defaults = items.value.filter(item => item.is_default && !item.blocked_reason)
      if (defaults.length === 1) selectedId.value = defaults[0]!.id
    } else if (selectedId.value !== 'manual' && !selected.value) {
      selectedId.value = ''; choose()
    }
  })
}
async function parse() {
  await run(async () => {
    const result = await parsePurchaseAddress(props.orderId, props.lineKey, raw.value)
    Object.assign(fields, result.address); warnings.value = result.warnings
    parsed.value = true; confirmed.value = false
  })
}
onMounted(() => { emit('change', null); void load() })
</script>

<template>
  <section aria-label="收货地址" class="space-y-3 rounded-xl border border-accent-200 p-4 dark:border-dark-600">
    <div class="flex items-center justify-between gap-3">
      <h3 class="font-semibold">收货地址</h3>
      <button type="button" class="btn btn-outline" :disabled="disabled || loading" @click="load">刷新地址</button>
    </div>
    <p v-if="loading" role="status" class="text-sm">正在读取地址…</p>
    <p v-if="notice" class="text-sm text-amber-700 dark:text-amber-300">{{ notice }}</p>
    <p v-if="error" role="alert" class="text-sm text-rose-700 dark:text-rose-300">{{ error }}</p>
    <fieldset :disabled="disabled || loading" class="space-y-3">
      <label class="block text-sm">选择地址
        <select v-model="selectedId" class="input mt-1" aria-label="选择收货地址" @change="choose">
          <option value="" disabled>请选择收货地址</option>
          <option v-for="item in items" :key="item.id" :value="item.id" :disabled="!!item.blocked_reason">
            {{ item.label }}{{ item.is_default ? '（默认）' : '' }} · {{ item.text.replace(/\n/g, ' ') }}{{ item.blocked_reason ? '（信息不完整）' : '' }}
          </option>
          <option value="manual">手动输入地址</option>
        </select>
      </label>
      <p v-if="saved" class="whitespace-pre-wrap text-sm">{{ selected?.text }}</p>
      <template v-if="editable">
        <label class="block text-sm">粘贴完整地址
          <textarea v-model="raw" aria-label="粘贴完整地址" class="input mt-1" rows="4" maxlength="4000" placeholder="例如：张三 13800138000 广东省深圳市南山区科技路1号" />
        </label>
        <button type="button" class="btn btn-outline" :disabled="!raw.trim()" @click="parse">解析地址</button>
        <template v-if="parsed">
          <p v-for="warning in warnings" :key="warning" role="status" class="text-sm text-amber-700 dark:text-amber-300">{{ warning }}</p>
          <div class="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <label v-for="[key, label] in entries" :key="key" class="block text-sm" :class="key === 'address' ? 'sm:col-span-2' : ''">{{ label }}
              <input v-model="fields[key]" class="input mt-1" :aria-label="label" :maxlength="key === 'address' ? 1000 : key === 'mobile' || key === 'phone' ? 40 : key === 'postCode' ? 20 : 100" />
            </label>
          </div>
          <p class="text-sm text-accent-600 dark:text-accent-300">请对照原文核对各字段；不会修改平台地址或地址备注。</p>
          <label class="flex gap-2 text-sm"><input v-model="confirmed" type="checkbox" :disabled="!complete" aria-label="确认收货地址" />已核对收货人、电话和完整地址</label>
        </template>
      </template>
    </fieldset>
  </section>
</template>
