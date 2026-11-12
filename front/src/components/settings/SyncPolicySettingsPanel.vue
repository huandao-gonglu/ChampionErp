<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { fetchSyncPolicies, saveSystemSettings } from '@/api/systemSettings'
import type { BackendSyncPolicyItem } from '@/types/workflow.generated'

const items = ref<BackendSyncPolicyItem[]>([])
const loading = ref(true), saving = ref(false), error = ref(''), message = ref('')
const original = ref('')
const editableValues = computed(() => Object.fromEntries(items.value.filter(item => item.editable).map(item => [item.key, item.values])))
const changed = computed(() => JSON.stringify(editableValues.value) !== original.value)
async function load() {
  loading.value = true
  error.value = ''
  try {
    items.value = (await fetchSyncPolicies()).items
    original.value = JSON.stringify(editableValues.value)
  } catch (exc) { error.value = exc instanceof Error ? exc.message : '同步策略读取失败' }
  finally { loading.value = false }
}
async function save() {
  if (loading.value || saving.value || !changed.value) return
  saving.value = true
  error.value = message.value = ''
  try {
    await saveSystemSettings({ sync_policies: editableValues.value })
    original.value = JSON.stringify(editableValues.value)
    message.value = '同步策略已保存'
  } catch (exc) { error.value = exc instanceof Error ? exc.message : '同步策略保存失败' }
  finally { saving.value = false }
}
onMounted(load)
</script>

<template>
  <form class="space-y-5" @submit.prevent="save">
    <p class="muted text-sm">设置自动重试与结果确认的间隔、次数和期限。达到确认上限后停止自动查询，保留平台实际结果，可手动核实。</p>
    <p v-if="loading" role="status">正在读取同步策略…</p>
    <p v-if="error" role="alert" class="text-sm text-red-600 dark:text-red-300">{{ error }} <button v-if="!items.length" type="button" class="underline" @click="load">重新读取</button></p>
    <fieldset v-for="item in items" :key="item.key" :disabled="loading || saving || !item.editable" class="space-y-3 rounded-lg border border-accent-200 p-4 dark:border-dark-700">
      <legend class="px-1 font-semibold">{{ item.title }}</legend>
      <p class="text-sm text-accent-600 dark:text-accent-300">{{ item.description }}</p>
      <p class="muted text-xs">{{ item.effect }}{{ item.editable ? '' : '；仅管理员可编辑' }}</p>
      <div class="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <label v-for="field in item.fields" :key="field.key" class="block text-sm" :for="`sync-${item.key}-${field.key}`">
          <span class="mb-1 block">{{ field.label }}（{{ field.unit }}）</span>
          <input :id="`sync-${item.key}-${field.key}`" v-model.number="item.values[field.key]" type="number" :min="field.minimum" :max="field.maximum" :step="field.step" required class="input w-full" @input="message = ''" />
          <span class="muted mt-1 block text-xs">{{ `默认 ${field.default}；范围 ${field.minimum}–${field.maximum}` }}</span>
        </label>
      </div>
    </fieldset>
    <p v-if="message" role="status" class="text-sm text-green-700 dark:text-green-300">{{ message }}</p>
    <div class="flex justify-end"><button type="submit" class="btn btn-primary" :disabled="loading || saving || !changed">{{ saving ? '保存中…' : '保存同步策略' }}</button></div>
  </form>
</template>
