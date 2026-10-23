<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { fetchYandexWarehouses, saveYandexWarehouses } from '@/api/yandexWarehouses'
import type { YandexWarehouseSettings, YandexWarehouseBinding } from '@/api/yandexWarehouses'
import type { Carrier } from '@/api/logistics'
const settings = ref<YandexWarehouseSettings | null>(null)
const bindings = ref<YandexWarehouseBinding[]>([])
const busy = ref(false), error = ref(''), notice = ref(''), editing = ref(false)
let disposed = false
const warehouses = computed(() => {
  const list = (settings.value?.warehouses || []).map(w => ({ ...w, available: true }))
  for (const item of bindings.value) {
    if (!list.some(w => w.id === item.warehouse_id)) list.push({ id: item.warehouse_id, name: `仓库 ${item.warehouse_id}`, available: false })
  }
  return list
})
function missingMode(item: YandexWarehouseBinding) {
  return !!item.shipping_mode && !settings.value?.modes.some(m => m.value === item.shipping_mode && m.carrier === item.carrier)
}
const canSave = computed(() => editing.value && bindings.value.every(b => !b.enabled || (b.shipping_mode && !missingMode(b))))
function removeBinding(warehouseId: number) {
  bindings.value = bindings.value.filter(b => b.warehouse_id !== warehouseId)
  editing.value = true
}
async function load(refresh = false) {
  busy.value = true; error.value = ''; notice.value = ''
  try {
    const data = await fetchYandexWarehouses(refresh)
    if (disposed) return
    settings.value = data; bindings.value = structuredClone(data.bindings); editing.value = false
  } catch (exc) { if (!disposed) error.value = exc instanceof Error ? exc.message : '发布仓库读取失败' }
  finally { if (!disposed) busy.value = false }
}
function changeCarrier(warehouseId: number, value: string) {
  const other = bindings.value.filter(b => b.warehouse_id !== warehouseId)
  bindings.value = value ? [...other, { warehouse_id: warehouseId, carrier: value as Carrier, shipping_mode: '', enabled: true }] : other
  editing.value = true
}
function binding(warehouseId: number) { return bindings.value.find(b => b.warehouse_id === warehouseId) }
async function save() {
  if (!settings.value || busy.value || !canSave.value) return
  busy.value = true; error.value = ''; notice.value = ''
  try {
    const data = await saveYandexWarehouses({ business_id: settings.value.account.business_id, campaign_id: settings.value.account.campaign_id,
      revision: settings.value.revision, bindings: bindings.value })
    if (disposed) return
    settings.value = data; bindings.value = structuredClone(data.bindings); editing.value = false
    notice.value = '仓库运输模式已保存，请重新核价后发布。'
  } catch (exc) { if (!disposed) error.value = exc instanceof Error ? exc.message : '发布仓库保存失败' }
  finally { if (!disposed) busy.value = false }
}
onMounted(() => load())
onBeforeUnmount(() => { disposed = true })
</script>
<template>
  <section class="space-y-4" aria-label="Yandex 核价与发布仓库">
    <div class="flex flex-wrap items-center justify-between gap-3"><div><h3 class="text-lg font-semibold">设置仓库运输模式</h3><p class="mt-1 text-sm text-accent-500">选择与 Yandex 后台一致的物流商和运输模式，例如 GUOO → Economy（陆运）。</p></div><button type="button" class="btn btn-secondary" :disabled="busy || editing" @click="load(true)">同步 Yandex 仓库</button></div>
    <p class="text-sm text-accent-500">核价使用所选模式的运费表，按商品重量、尺寸和货值计算；发布库存时使用核价选中的仓库。这里的设置不会修改 Yandex 后台。</p>
    <p v-if="busy" role="status">正在处理发布仓库…</p>
    <p v-if="error || settings?.error" role="alert" class="text-sm text-amber-700 dark:text-amber-300">{{ error || settings?.error }}</p>
    <p v-if="notice" role="status" class="text-sm text-primary-700 dark:text-primary-300">{{ notice }}</p>
    <p v-if="settings && !settings.warehouses.length" class="text-sm text-accent-500">尚无可用的远端发布仓库，请同步仓库并检查店铺授权。</p>
    <p v-if="settings?.checked_at" class="text-xs text-accent-500">最近同步：{{ new Date(settings.checked_at).toLocaleString('zh-CN') }}</p>
    <form v-if="settings && (warehouses.length || editing)" class="space-y-4" @submit.prevent="save">
      <fieldset :disabled="busy" class="space-y-4">
        <article v-for="warehouse in warehouses" :key="warehouse.id" class="space-y-3 rounded border border-accent-200 p-4 dark:border-accent-700">
          <div class="flex flex-wrap items-center justify-between gap-2"><h4 class="font-semibold">{{ warehouse.name }} <span class="text-xs font-normal text-accent-500">ID {{ warehouse.id }}</span></h4><button v-if="binding(warehouse.id)" type="button" class="text-sm text-accent-500 underline" @click="removeBinding(warehouse.id)">解除关联</button></div>
          <p v-if="!warehouse.available" class="text-sm text-amber-700 dark:text-amber-300">远端仓库未返回或已不可用，当前不参与核价。请重新同步，或解除关联后保存。</p>
          <label v-if="warehouse.available" class="block text-sm">物流商<select :value="binding(warehouse.id)?.carrier || ''" :aria-label="`${warehouse.name} 物流商`" class="input mt-1 w-full" @change="changeCarrier(warehouse.id, ($event.target as HTMLSelectElement).value)"><option value="">请选择物流商</option><option v-for="carrier in settings.carriers" :key="carrier.value" :value="carrier.value">{{ carrier.label }}</option></select></label>
          <template v-for="item in bindings.filter(b => b.warehouse_id === warehouse.id)" :key="item.warehouse_id">
            <label class="block text-sm"><input v-model="item.enabled" type="checkbox" @change="editing = true" /> 参与自动核价</label>
            <label class="block text-sm">运输模式
              <select v-model="item.shipping_mode" :aria-label="`${warehouse.name} 运输模式`" :disabled="!warehouse.available" class="input mt-1 w-full" @change="editing = true">
                <option value="">请选择运输模式</option>
                <option v-if="missingMode(item)" :value="item.shipping_mode" disabled>{{ item.shipping_mode }}（当前运费表不可用）</option>
                <option v-for="mode in settings.modes.filter(m => m.carrier === item.carrier)" :key="mode.value" :value="mode.value">{{ mode.value }} · {{ mode.transport }}</option>
              </select>
            </label>
            <p v-if="!settings.modes.some(m => m.carrier === item.carrier)" class="text-sm text-amber-700 dark:text-amber-300">当前运费表没有此物流商的运输模式。</p>
            <p v-if="missingMode(item)" class="text-sm text-amber-700 dark:text-amber-300">原运输模式已不可用，请重新选择或停用此仓库。</p>
            <p v-if="!item.shipping_mode" class="text-sm text-amber-700 dark:text-amber-300">尚未选择运输模式，此仓库暂不参与核价。</p>
            <p v-for="warning in settings.binding_warnings.filter(w => w.warehouse_id === warehouse.id)" v-show="!item.shipping_mode" :key="warning.warehouse_id" class="text-sm text-amber-700 dark:text-amber-300">{{ warning.message }}</p>
          </template>
          <p class="text-xs text-accent-500">每个仓库选择一种运输模式；Extra Small 为独立轻小件服务，请与 Yandex 后台配置保持一致。</p>
        </article>
        <div class="flex gap-3"><button type="submit" class="btn btn-primary" :disabled="!canSave">保存发布仓库配置</button><button v-if="editing" type="button" class="btn btn-secondary" @click="load()">取消修改</button></div>
      </fieldset>
    </form>
  </section>
</template>
