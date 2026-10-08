<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import PageHeader from '@/components/layout/PageHeader.vue'
import { fetchSystemSettings, saveSystemSettings } from '@/api/systemSettings'

const hours = ref(5)
const savedHours = ref<number | null>(null)
const loading = ref(true)
const saving = ref(false)
const error = ref('')
const message = ref('')
const valid = computed(() => Number.isInteger(hours.value) && hours.value >= 5)
async function load() {
  loading.value = true
  error.value = ''
  try {
    const settings = await fetchSystemSettings()
    hours.value = savedHours.value = settings.orders_auto_sync_interval_hours
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : '系统设置读取失败'
  } finally { loading.value = false }
}
async function save() {
  if (saving.value || savedHours.value === null || !valid.value) return
  saving.value = true
  error.value = message.value = ''
  try {
    const settings = await saveSystemSettings({ orders_auto_sync_interval_hours: hours.value })
    hours.value = savedHours.value = settings.orders_auto_sync_interval_hours
    message.value = '设置已保存'
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : '系统设置保存失败'
  } finally { saving.value = false }
}
onMounted(load)
</script>

<template>
  <section class="space-y-6">
    <PageHeader title="系统设置" />
    <form id="system-settings-form" class="space-y-5 rounded-3xl border border-accent-200 bg-white p-6 text-accent-950 shadow-card dark:border-white/10 dark:bg-dark-900/80 dark:text-accent-100" @submit.prevent="save">
      <p v-if="loading" role="status" class="text-sm text-accent-500">正在读取设置…</p>
      <p v-if="error" role="alert" class="text-sm text-red-600 dark:text-red-400">{{ error }} <button v-if="savedHours === null" type="button" class="underline" :disabled="loading" @click="load">重试</button></p>
      <fieldset :disabled="loading || saving || savedHours === null" class="space-y-3">
        <legend class="mb-3 font-semibold">订单同步</legend>
        <label for="orders-auto-sync-hours" class="block text-sm font-medium">自动同步间隔</label>
        <div class="flex items-center gap-3">
          <input id="orders-auto-sync-hours" v-model.number="hours" type="number" min="5" step="1" required class="input w-32" aria-describedby="orders-auto-sync-help" @input="message = ''" />
          <span class="text-sm">小时</span>
        </div>
        <p id="orders-auto-sync-help" class="max-w-3xl text-sm leading-6 text-accent-500 dark:text-accent-300">至少 5 小时。进入订单中心时，距离上次自动同步达到此间隔才会同步订单、采购和仓库状态。手动同步不受限制。</p>
      </fieldset>
      <p v-if="message" role="status" class="text-sm text-green-700 dark:text-green-400">{{ message }}</p>
      <div class="flex justify-end border-t border-accent-200 pt-5 dark:border-white/10">
        <button type="submit" form="system-settings-form" class="btn btn-primary" :disabled="loading || saving || savedHours === null || !valid || hours === savedHours">{{ saving ? '保存中…' : '保存设置' }}</button>
      </div>
    </form>
  </section>
</template>
