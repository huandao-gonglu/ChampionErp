<script setup lang="ts">
import { onBeforeUnmount, ref } from 'vue'
import { parseLogisticsAddress } from '@/api/logistics'
import type { LogisticsAddressInput, LogisticsSettings } from '@/api/logistics'
import type { PurchaseAddressFields } from '@/api/alibabaSelfPurchase'
const props = defineProps<{ initial: LogisticsAddressInput; settings: LogisticsSettings; busy: boolean }>()
const emit = defineEmits<{ save: [LogisticsAddressInput]; cancel: [] }>()
const form = ref<LogisticsAddressInput>(JSON.parse(JSON.stringify(props.initial)))
const rawAddress = ref(''), parsing = ref(false), parseError = ref(''), parsed = ref(false)
const warnings = ref<string[]>([])
let disposed = false
async function parse() {
  if (props.busy || parsing.value || !rawAddress.value.trim()) return
  parsing.value = true; parseError.value = ''; parsed.value = false; warnings.value = []
  try {
    const result = await parseLogisticsAddress(rawAddress.value)
    if (disposed) return
    form.value.address = { ...result.address }
    warnings.value = result.warnings; parsed.value = true
  } catch (exc) { if (!disposed) parseError.value = exc instanceof Error ? exc.message : '地址解析失败，请重试' }
  finally { if (!disposed) parsing.value = false }
}
function save() { if (!props.busy && !parsing.value) emit('save', form.value) }
onBeforeUnmount(() => { disposed = true })
const fields: Array<[keyof PurchaseAddressFields, string]> = [['fullName', '收货人'], ['mobile', '手机号'], ['phone', '固定电话'], ['provinceText', '省'], ['cityText', '市'], ['areaText', '区／县'], ['townText', '街道／乡镇'], ['address', '详细地址'], ['postCode', '邮编']]
</script>
<template>
  <form class="mt-4 space-y-4 rounded-lg border border-accent-200 p-4 dark:border-accent-700" aria-label="物流收货地址" @submit.prevent="save">
    <fieldset :disabled="busy || parsing" class="space-y-4">
      <h3 class="font-semibold">{{ form.id ? '编辑收货地址' : '新增收货地址' }}</h3>
      <label class="block">所属合作仓库<select v-model.number="form.warehouse_id" aria-label="所属合作仓库" class="input mt-1 w-full" required><option v-if="!settings.warehouses.some(w => w.id === form.warehouse_id)" :value="form.warehouse_id">{{ initial.warehouse_id }}（合作已失效）</option><option v-for="warehouse in settings.warehouses" :key="warehouse.id" :value="warehouse.id">{{ warehouse.name }} · ID {{ warehouse.id }}</option></select></label>
      <p v-if="form.id" class="text-sm text-accent-500">保存修改后，引用此配置的订单须重新确认收货仓及报单绑定，才能继续报单；原采购记录保留。</p>
      <label class="block">地址名称<input v-model.trim="form.name" aria-label="地址名称" class="input mt-1 w-full" maxlength="100" required placeholder="例如：义乌收货点" /></label>
      <div class="space-y-2 rounded-lg border border-accent-200 p-3 dark:border-accent-700">
        <label class="block">粘贴完整地址<textarea v-model="rawAddress" aria-label="粘贴完整地址" class="input mt-1 w-full" rows="3" maxlength="4000" placeholder="例如：张三 13800138000 广东省深圳市南山区科技路1号" @input="parsed = false; warnings = []; parseError = ''" /></label>
        <button type="button" class="btn btn-secondary" :disabled="!rawAddress.trim()" @click="parse">{{ parsing ? '正在解析…' : '解析并填入地址' }}</button>
        <p v-if="parseError" role="alert" class="text-sm text-red-600 dark:text-red-400">{{ parseError }}</p>
        <p v-if="parsed" role="status" class="text-sm text-accent-500">已填入下方地址字段，请核对后保存；未识别的字段请手动补全。</p>
        <p v-for="warning in warnings" :key="warning" role="status" class="text-sm text-amber-700 dark:text-amber-300">{{ warning }}</p>
      </div>
      <div class="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <label v-for="[key, label] in fields" :key="key" :class="key === 'address' ? 'sm:col-span-2' : ''">{{ label }}<input v-model.trim="form.address[key]" :aria-label="label" class="input mt-1 w-full" :required="['fullName', 'provinceText', 'cityText', 'areaText', 'address'].includes(key)" :maxlength="key === 'address' ? 1000 : key === 'postCode' ? 20 : 100" /></label>
      </div>
      <p class="text-xs text-accent-500">手机号和固定电话至少填写一项。</p>
      <label class="block">收货备注<textarea v-model.trim="form.remark" aria-label="收货备注" class="input mt-1 w-full" maxlength="1000" placeholder="收货时间、包裹备注要求等" /></label>
      <div class="flex flex-wrap gap-4"><label><input v-model="form.enabled" type="checkbox" /> 启用此地址</label><label><input v-model="form.is_default" type="checkbox" /> 设为该仓默认地址</label></div>
      <fieldset class="space-y-3" aria-label="支持的运输服务">
        <legend class="font-semibold">支持的运输服务（物流商）</legend>
        <p class="text-sm text-accent-500">Yandex：勾选此收货点支持的物流商，可多选。采购时按平台订单的物流商筛选地址。</p>
        <div class="flex flex-wrap gap-4">
          <label v-for="option in settings.carriers" :key="option.value">
            <input type="checkbox" :aria-label="option.label" :checked="form.rules.some(r => r.platform === option.platform && r.carrier === option.value)" @change="form.rules = ($event.target as HTMLInputElement).checked ? [...form.rules, { platform: option.platform, carrier: option.value }] : form.rules.filter(r => r.carrier !== option.value || r.platform !== option.platform)" /> {{ option.label }}
          </label>
        </div>
        <p v-if="!form.rules.length" class="text-sm text-accent-500">尚未选择物流商，此地址不会出现在采购候选中。</p>
      </fieldset>
      <div class="flex gap-3"><button class="btn btn-primary" type="submit">保存地址</button><button class="btn btn-secondary" type="button" @click="emit('cancel')">取消</button></div>
    </fieldset>
  </form>
</template>
