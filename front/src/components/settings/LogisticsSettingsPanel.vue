<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { emptyLogisticsAddress, fetchLogisticsSettings, saveLogisticsAddress } from '@/api/logistics'
import type { LogisticsAddress, LogisticsAddressInput, LogisticsSettings } from '@/api/logistics'
import LogisticsAddressEditor from './LogisticsAddressEditor.vue'
const settings = ref<LogisticsSettings | null>(null)
const warehouseId = ref(0), busy = ref(false), error = ref(''), notice = ref('')
const editing = ref<LogisticsAddressInput | null>(null)
let disposed = false
const warehouses = computed(() => {
  const list = settings.value?.warehouses.map(w => ({ id: w.id, name: w.name, available: true })) || []
  for (const a of settings.value?.addresses || []) if (!list.some(w => w.id === a.warehouse_id)) list.push({ id: a.warehouse_id, name: a.warehouse_name, available: false })
  return list
})
const addresses = computed(() => settings.value?.addresses.filter(a => a.warehouse_id === warehouseId.value) || [])
async function run(action: () => Promise<LogisticsSettings>) {
  busy.value = true; error.value = ''; notice.value = ''
  try { const value = await action(); if (!disposed) { settings.value = value; if (!warehouseId.value) warehouseId.value = warehouses.value[0]?.id || 0 } }
  catch (exc) { if (!disposed) error.value = exc instanceof Error ? exc.message : '物流设置读取失败' }
  finally { if (!disposed) busy.value = false }
}
async function save(value: LogisticsAddressInput) {
  await run(() => saveLogisticsAddress(value))
  if (!error.value && !disposed) { editing.value = null; notice.value = '收货地址已保存，已采购订单仍使用原地址快照。' }
}
function edit(value: LogisticsAddress) {
  editing.value = JSON.parse(JSON.stringify(Object.fromEntries(Object.keys(emptyLogisticsAddress(value.warehouse_id)).map(k => [k, value[k as keyof LogisticsAddress]])))) as LogisticsAddressInput
}
onMounted(() => run(() => fetchLogisticsSettings(true)))
onBeforeUnmount(() => { disposed = true })
</script>
<template>
  <section class="space-y-4" aria-label="物流设置">
    <div class="flex flex-wrap items-center justify-between gap-3"><div><h3 class="text-lg font-semibold">物流设置</h3><p class="text-sm text-accent-500">按合作仓维护收货地址及承接范围，采购时筛选具体地址。</p></div><button class="btn btn-secondary" :disabled="busy || !!editing" @click="run(() => fetchLogisticsSettings(true))">同步合作仓库</button></div>
    <p v-if="busy" role="status">正在处理物流资料…</p><p v-if="error" role="alert" class="text-red-600">{{ error }}</p><p v-if="notice" role="status">{{ notice }}</p>
    <template v-if="settings">
      <p v-if="!settings.authorized">请先在跨境巴士设置中完成授权。</p>
      <p v-else-if="!settings.warehouses.length" class="text-sm text-accent-500">当前账号暂无已合作仓库，请在跨境巴士确认合作关系后同步。</p>
      <label>合作仓库<select v-model.number="warehouseId" aria-label="合作仓库" class="input ml-3" :disabled="busy || !!editing"><option :value="0" disabled>请选择合作仓库</option><option v-for="w in warehouses" :key="w.id" :value="w.id">{{ w.name }} · ID {{ w.id }}{{ w.available ? '' : '（合作已失效）' }}</option></select></label>
      <div v-if="warehouseId && !editing" class="space-y-3">
        <button class="btn btn-primary" :disabled="busy || !warehouses.find(w => w.id === warehouseId)?.available" @click="editing = emptyLogisticsAddress(warehouseId)">新增收货地址</button>
        <p v-if="!addresses.length" class="text-sm text-accent-500">此合作仓尚未配置收货地址。</p>
        <article v-for="a in addresses" :key="a.id" class="rounded border border-accent-200 p-4 dark:border-accent-700">
          <div class="flex justify-between gap-3"><strong>{{ a.name }}{{ a.is_default ? ' · 默认' : '' }}{{ a.enabled ? '' : ' · 已停用' }}</strong><button class="btn btn-secondary" :disabled="busy" @click="edit(a)">编辑地址</button></div>
          <p class="mt-2">{{ a.address.fullName }} · {{ a.address.mobile || a.address.phone }}</p>
          <p>{{ [a.address.provinceText, a.address.cityText, a.address.areaText, a.address.townText, a.address.address].filter(Boolean).join(' ') }}</p>
          <p v-if="a.remark" class="mt-2 whitespace-pre-wrap">{{ a.remark }}</p>
          <p v-if="a.migrated" class="mt-2 text-sm text-amber-600">由原地址迁入，请配置支持的物流商后使用。</p>
          <p v-for="(rule, i) in a.rules" :key="i" class="mt-2 text-sm">Yandex · {{ settings.carriers.find(c => c.value === rule.carrier)?.label }}</p>
          <p v-if="!a.rules.length" class="mt-2 text-sm text-accent-500">尚未配置支持的物流商。</p>
        </article>
      </div>
      <LogisticsAddressEditor v-if="editing" :key="editing.id || 'new'" :initial="editing" :settings="settings" :busy="busy" @save="save" @cancel="editing = null" />
    </template>
  </section>
</template>
