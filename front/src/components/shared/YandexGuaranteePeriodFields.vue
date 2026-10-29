<script setup lang="ts">
import { computed, watch } from 'vue'
import { yandexWarrantyUnits as units, yandexWarrantyError, type YandexGuaranteePeriod } from '@/utils/yandexWarranty'

const props = defineProps<{ modelValue: YandexGuaranteePeriod; required?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: YandexGuaranteePeriod]; validity: [valid: boolean] }>()
const error = computed(() => yandexWarrantyError(props.modelValue, props.required))
watch(error, value => emit('validity', !value), { immediate: true })
function update(field: string, value: string) {
  const next = { ...props.modelValue, [field]: value }
  emit('update:modelValue', Object.values(next).some(value => value !== '') ? next : {})
}
</script>

<template>
  <div>
    <div class="mt-3 grid gap-3 sm:grid-cols-[7rem_8rem_minmax(0,1fr)]">
      <label class="block text-xs font-semibold">时长
        <input :value="modelValue.timePeriod ?? ''" type="number" min="0" step="1" class="input mt-1" aria-label="Yandex 保修时长" data-publish-draft-field="guaranteePeriod" @input="update('timePeriod', ($event.target as HTMLInputElement).value)" />
      </label>
      <label class="block text-xs font-semibold">单位
        <select :value="modelValue.timeUnit || ''" class="input mt-1" aria-label="Yandex 保修单位" @change="update('timeUnit', ($event.target as HTMLSelectElement).value)">
          <option value="">请选择单位</option>
          <option v-for="[value, label] in units" :key="value" :value="value">{{ label }}</option>
        </select>
      </label>
      <label class="block text-xs font-semibold">保修说明（选填）
        <input :value="modelValue.comment || ''" maxlength="500" class="input mt-1" aria-label="Yandex 保修说明" @input="update('comment', ($event.target as HTMLInputElement).value)" />
      </label>
    </div>
    <p v-if="error" role="alert" class="mt-2 text-xs text-rose-700">{{ error }}</p>
  </div>
</template>
