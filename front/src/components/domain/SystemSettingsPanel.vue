<script setup lang="ts">
import { computed, inject, onMounted, ref, watch } from 'vue'
import { routeLocationKey } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useAppStore } from '@/stores/app'
import { UI_LOCALE_OPTIONS, type SupportedUiLocale } from '@/constants/locales'
import ExternalRequestControlPanel from '@/components/settings/ExternalRequestControlPanel.vue'
import AboutSettingsPanel from '@/components/settings/AboutSettingsPanel.vue'
import YandexWarehouseSettings from '@/components/settings/YandexWarehouseSettings.vue'
import WatermarkSettingsPanel from '@/components/settings/WatermarkSettingsPanel.vue'
import WechatSettingsPanel from '@/components/settings/WechatSettingsPanel.vue'
import { fetchSystemSettings, saveSystemSettings } from '@/api/systemSettings'

const { t } = useI18n()
const appStore = useAppStore()
const colorMode = computed({
  get: () => appStore.theme,
  set: (value: 'light' | 'dark') => {
    if (value !== appStore.theme) appStore.toggleTheme()
  },
})
const tabs = computed(() => [
  { key: 'general', label: '通用设置', summary: '颜色、界面语言与订单自动同步' },
  { key: 'wechat', label: t('settings.wechat'), summary: t('settings.wechatSummary') },
  { key: 'watermark', label: '图片水印', summary: '发布水印、文字与自定义图片' },
  { key: 'yandex_warehouses', label: 'Yandex 发布仓库', summary: '同步远端仓库并指定运输模式' },
  { key: 'interruptions', label: '中断与恢复', summary: '全部请求中断、冷却时间与手动恢复' },
  { key: 'about', label: t('settings.about'), summary: t('settings.aboutSummary') },
] as const)
const section = ref<'general' | 'wechat' | 'watermark' | 'yandex_warehouses' | 'interruptions' | 'about'>('general')
const route = inject(routeLocationKey, undefined)
watch(() => route?.query.settings_section, value => {
  section.value = value === 'wechat' || value === 'interruptions' || value === 'about' || value === 'yandex_warehouses' || value === 'watermark' ? value : 'general'
}, { immediate: true })
const activeTab = computed(() => tabs.value.find(tab => tab.key === section.value) || tabs.value[0])

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
  <section class="rounded-lg border border-accent-200 bg-white p-5 shadow-card dark:border-dark-700 dark:bg-dark-900/80">
    <h2 class="card-title">系统设置</h2>
    <p class="muted mt-1">{{ activeTab.summary }}</p>
    <div class="mt-5 overflow-x-auto rounded-lg border border-accent-200 bg-accent-50 p-1 dark:border-dark-700 dark:bg-dark-950/70">
      <nav class="flex min-w-max gap-1" role="tablist" aria-label="系统设置分类">
        <button
          v-for="tab in tabs"
          :id="`system-settings-tab-${tab.key}`"
          :key="tab.key"
          type="button"
          class="min-w-32 rounded-lg px-4 py-3 text-left text-sm transition focus:outline-none focus:ring-2 focus:ring-primary-300"
          :class="section === tab.key ? 'border border-primary-200 bg-white text-primary-700 shadow-sm dark:border-primary-500/40 dark:bg-primary-500/10 dark:text-primary-100' : 'border border-transparent text-accent-600 hover:bg-white/80 hover:text-accent-950 dark:text-accent-300 dark:hover:bg-dark-900 dark:hover:text-white'"
          role="tab"
          :aria-selected="section === tab.key"
          :aria-controls="`system-settings-panel-${tab.key}`"
          @click="section = tab.key"
        >
          <span class="block whitespace-nowrap font-semibold">{{ tab.label }}</span>
          <span class="mt-1 hidden whitespace-nowrap text-xs text-accent-500 dark:text-accent-400 md:block">{{ tab.summary }}</span>
        </button>
      </nav>
    </div>
    <section v-if="section === 'wechat'" id="system-settings-panel-wechat" class="mt-5" role="tabpanel" aria-labelledby="system-settings-tab-wechat">
      <WechatSettingsPanel />
    </section>
    <section v-if="section === 'watermark'" id="system-settings-panel-watermark" class="mt-5" role="tabpanel" aria-labelledby="system-settings-tab-watermark">
      <WatermarkSettingsPanel />
    </section>
    <section v-if="section === 'interruptions'" id="system-settings-panel-interruptions" class="mt-5" role="tabpanel" aria-labelledby="system-settings-tab-interruptions">
      <ExternalRequestControlPanel />
    </section>
    <section v-if="section === 'about'" id="system-settings-panel-about" class="mt-5" role="tabpanel" aria-labelledby="system-settings-tab-about">
      <AboutSettingsPanel />
    </section>
    <section v-if="section === 'yandex_warehouses'" id="system-settings-panel-yandex_warehouses" class="mt-5" role="tabpanel" aria-labelledby="system-settings-tab-yandex_warehouses">
      <YandexWarehouseSettings />
    </section>
    <section v-show="section === 'general'" id="system-settings-panel-general" class="mt-5 space-y-5" role="tabpanel" aria-labelledby="system-settings-tab-general">
      <section class="space-y-4 rounded-lg border border-accent-200 bg-accent-50 p-4 dark:border-dark-700 dark:bg-dark-950/70" aria-labelledby="appearance-settings-title">
        <h3 id="appearance-settings-title" class="font-semibold">{{ t('settings.appearanceAndLanguage') }}</h3>
        <div class="grid gap-4 sm:grid-cols-2">
          <label for="settings-color-mode" class="block text-sm font-medium">
            <span class="mb-2 block">{{ t('settings.colorMode') }}</span>
            <select id="settings-color-mode" v-model="colorMode" class="input">
              <option value="light">{{ t('settings.lightMode') }}</option>
              <option value="dark">{{ t('settings.darkMode') }}</option>
            </select>
          </label>
          <label for="settings-ui-language" class="block text-sm font-medium">
            <span class="mb-2 block">{{ t('settings.uiLanguage') }}</span>
            <select id="settings-ui-language" :value="appStore.uiLocale" class="input" @change="appStore.setUiLocale(($event.target as HTMLSelectElement).value as SupportedUiLocale)">
              <option v-for="locale in UI_LOCALE_OPTIONS" :key="locale.value" :value="locale.value">{{ locale.label }}</option>
            </select>
          </label>
        </div>
        <p class="muted text-sm">{{ t('settings.preferencesAutoSaved') }}</p>
      </section>
      <form id="system-settings-form" class="space-y-5 rounded-lg border border-accent-200 bg-accent-50 p-4 dark:border-dark-700 dark:bg-dark-950/70" @submit.prevent="save">
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
  </section>
</template>
