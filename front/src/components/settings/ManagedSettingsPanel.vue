<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { fetchManagedSettings, saveManagedSettings, type ManagedSettings } from '@/api/managedSettings'
import { testApiConfig } from '@/api/workflow/settings'

const settings = ref<ManagedSettings | null>(null)
const secrets = ref({ alibaba: '', alibaba_crossborder: '', onebound: '', crossborderbus: '' })
const applications = [
  { key: 'alibaba', title: '1688 企业自用应用' },
  { key: 'alibaba_crossborder', title: '1688 跨境 ERP 服务商应用' },
] as const
const busy = ref(false)
const error = ref('')
const notice = ref('')

async function load() {
  busy.value = true
  error.value = ''
  try { settings.value = await fetchManagedSettings() }
  catch (cause) { error.value = cause instanceof Error ? cause.message : '共享设置读取失败' }
  finally { busy.value = false }
}
async function save() {
  if (!settings.value || busy.value) return
  busy.value = true
  error.value = notice.value = ''
  try {
    const value = settings.value
    settings.value = await saveManagedSettings({
      revision: value.revision, exchange_rates: value.exchange_rates,
      alibaba_api_application: value.alibaba_api_application,
      alibaba: {
        app_key: value.alibaba.app_key, redirect_uri: value.alibaba.redirect_uri,
        ...(secrets.value.alibaba.trim() ? { app_secret: secrets.value.alibaba.trim() } : {}),
      },
      alibaba_crossborder: {
        app_key: value.alibaba_crossborder.app_key, redirect_uri: value.alibaba_crossborder.redirect_uri,
        ...(secrets.value.alibaba_crossborder.trim() ? { app_secret: secrets.value.alibaba_crossborder.trim() } : {}),
      },
      onebound: {
        api_key: value.onebound.api_key,
        ...(secrets.value.onebound.trim() ? { api_secret: secrets.value.onebound.trim() } : {}),
      },
      ...(secrets.value.crossborderbus.trim() ? { crossborderbus_client_secret: secrets.value.crossborderbus.trim() } : {}),
    })
    secrets.value = { alibaba: '', alibaba_crossborder: '', onebound: '', crossborderbus: '' }
    notice.value = '共享设置已保存'
  } catch (cause) { error.value = cause instanceof Error ? cause.message : '共享设置保存失败' }
  finally { busy.value = false }
}
async function testRates() {
  if (busy.value) return
  busy.value = true
  error.value = notice.value = ''
  try {
    const result = await testApiConfig('exchange_rate', {})
    if (!result.ok) throw new Error(result.error || result.message || '汇率 API 测试失败')
    notice.value = result.message || '汇率 API 测试通过'
  } catch (cause) { error.value = cause instanceof Error ? cause.message : '汇率 API 测试失败' }
  finally { busy.value = false }
}
onMounted(load)
</script>

<template>
  <form class="space-y-6" :aria-busy="busy" @submit.prevent="save">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <h3 class="font-semibold">管理员共享设置</h3>
      <button type="button" class="btn btn-secondary" :disabled="busy" @click="load">重新读取</button>
    </div>
    <p v-if="error" role="alert" class="text-sm text-red-600 dark:text-red-400">{{ error }}</p>
    <p v-if="notice" role="status" class="text-sm text-green-700 dark:text-green-400">{{ notice }}</p>
    <p v-if="busy && !settings" role="status">正在读取共享设置…</p>
    <fieldset v-if="settings" :disabled="busy" class="space-y-6">
      <section class="space-y-4 rounded-lg border border-accent-200 p-4 dark:border-dark-700">
        <div class="flex flex-wrap items-center justify-between gap-3"><h4 class="font-semibold">汇率 API</h4><button type="button" class="btn btn-secondary" @click="testRates">测试已保存配置</button></div>
        <p class="muted text-sm">所有账号使用此服务获取汇率。核价时可按实际需要调整汇率数字。</p>
        <label class="block text-sm">API 地址<input v-model="settings.exchange_rates.exchange_rate_api_url" type="url" required maxlength="2048" class="input mt-2 w-full" /></label>
        <div class="grid gap-4 sm:grid-cols-2">
          <label class="block text-sm">超时时间（秒）<input v-model.number="settings.exchange_rates.exchange_rate_timeout_seconds" type="number" min="1" max="120" required class="input mt-2 w-full" /></label>
          <label class="block text-sm">缓存时间（秒）<input v-model.number="settings.exchange_rates.exchange_rate_cache_ttl_seconds" type="number" min="0" max="86400" required class="input mt-2 w-full" /></label>
        </div>
      </section>
      <section class="space-y-4 rounded-lg border border-accent-200 p-4 dark:border-dark-700">
        <h4 class="font-semibold">1688 API</h4>
        <label class="block text-sm">当前使用的应用
          <select v-model="settings.alibaba_api_application" class="input mt-2 w-full" aria-label="当前使用的 1688 应用">
            <option v-for="application in applications" :key="application.key" :value="application.key">{{ application.title }}</option>
            <option value="onebound">万邦 1688 API</option>
          </select>
        </label>
        <p class="muted text-sm">所有账号的商品采集、采购、支付和物流查询使用所选方案。三套方案的账号授权分别保留，历史采购归属不变。</p>
      </section>
      <p class="text-sm text-amber-700 dark:text-amber-300">更换企业应用凭据后，对应的用户授权将失效，需要重新连接账号。已保存的密钥留空可继续使用。</p>
      <section v-for="application in applications" :key="application.key" class="space-y-4 rounded-lg border border-accent-200 p-4 dark:border-dark-700">
        <h4 class="font-semibold">{{ application.title }}</h4>
        <label class="block text-sm">应用编号<input v-model="settings[application.key].app_key" maxlength="256" autocomplete="off" class="input mt-2 w-full" /></label>
        <label class="block text-sm">应用密钥<input v-model="secrets[application.key]" type="password" maxlength="4096" autocomplete="new-password" :placeholder="settings[application.key].secret_configured ? '已配置，填写可更换' : '尚未配置'" class="input mt-2 w-full" /></label>
        <label class="block text-sm">授权回调地址<input v-model="settings[application.key].redirect_uri" type="url" placeholder="https://你的ERP域名/auth/alibaba/callback" maxlength="2048" class="input mt-2 w-full" /></label>
      </section>
      <section class="space-y-4 rounded-lg border border-accent-200 p-4 dark:border-dark-700" aria-label="万邦企业配置">
        <h4 class="font-semibold">万邦 1688 API</h4>
        <p class="muted text-sm">Key 和 Secret 由管理员统一维护。买家授权需使用万邦对应的 1688 授权，不能复用官方应用的 Token。买家授权回调尚未接入。</p>
        <label class="block text-sm">API Key<input v-model="settings.onebound.api_key" maxlength="256" autocomplete="off" class="input mt-2 w-full" /></label>
        <label class="block text-sm">API Secret<input v-model="secrets.onebound" type="password" maxlength="4096" autocomplete="new-password" :placeholder="settings.onebound.secret_configured ? '已配置，填写可更换' : '尚未配置'" class="input mt-2 w-full" /></label>
      </section>
      <section class="space-y-4 rounded-lg border border-accent-200 p-4 dark:border-dark-700">
        <h4 class="font-semibold">跨境巴士企业应用</h4>
        <label class="block text-sm">企业密钥<input v-model="secrets.crossborderbus" type="password" maxlength="4096" autocomplete="new-password" :placeholder="settings.crossborderbus_secret_configured ? '已配置，填写可更换' : '尚未配置'" class="input mt-2 w-full" /></label>
      </section>
      <button type="submit" class="btn btn-primary">{{ busy ? '保存中…' : '保存共享设置' }}</button>
    </fieldset>
  </form>
</template>
