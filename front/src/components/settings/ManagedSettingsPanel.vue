<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { fetchOneBoundQuota, restoreOneBoundKey, type OneBoundQuotaView } from '@/api/alibabaAuthorization'
import { fetchManagedSettings, saveManagedSettings, type ManagedSettings } from '@/api/managedSettings'
import { testApiConfig } from '@/api/workflow/settings'

const settings = ref<ManagedSettings | null>(null)
const oneboundQuota = ref<OneBoundQuotaView | null>(null)
const secrets = ref({ alibaba: '', alibaba_crossborder: '', onebound: '', crossborderbus: '' })
const applications = [
  { key: 'alibaba', title: '1688 企业自用应用' },
  { key: 'alibaba_crossborder', title: '1688 跨境 ERP 服务商应用' },
] as const
const operation = ref<'load' | 'save' | 'test' | 'restore' | null>(null)
const error = ref('')
const notice = ref('')

async function loadOneBoundQuota() {
  try { oneboundQuota.value = await fetchOneBoundQuota() }
  catch { /* 额度只读状态不可用时不阻塞共享设置管理。 */ }
}
async function load() {
  operation.value = 'load'
  error.value = notice.value = ''
  try {
    settings.value = await fetchManagedSettings()
    void loadOneBoundQuota()
  }
  catch (cause) { error.value = cause instanceof Error ? cause.message : '共享设置读取失败' }
  finally { operation.value = null }
}
async function restoreKey() {
  if (operation.value) return
  operation.value = 'restore'
  error.value = notice.value = ''
  try {
    const result = await restoreOneBoundKey()
    oneboundQuota.value = result.quota
    notice.value = result.restored ? '已恢复万邦企业 Key 使用' : '万邦企业 Key 当前已处于可用状态'
  } catch (cause) { error.value = cause instanceof Error ? cause.message : '恢复万邦企业 Key 失败' }
  finally { operation.value = null }
}
async function save() {
  if (!settings.value || operation.value) return
  operation.value = 'save'
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
        initial_grant: value.onebound.initial_grant,
        ...(secrets.value.onebound.trim() ? { api_secret: secrets.value.onebound.trim() } : {}),
      },
      ...(secrets.value.crossborderbus.trim() ? { crossborderbus_client_secret: secrets.value.crossborderbus.trim() } : {}),
      gemini_canvas_url: value.gemini_canvas_url,
    })
    secrets.value = { alibaba: '', alibaba_crossborder: '', onebound: '', crossborderbus: '' }
    notice.value = '共享设置已保存'
    void loadOneBoundQuota()
  } catch (cause) { error.value = cause instanceof Error ? cause.message : '共享设置保存失败' }
  finally { operation.value = null }
}
async function testRates() {
  if (operation.value) return
  operation.value = 'test'
  error.value = notice.value = ''
  try {
    const result = await testApiConfig('exchange_rate', {})
    if (!result.ok) throw new Error(result.error || result.message || '汇率 API 测试失败')
    notice.value = result.message || '汇率 API 测试通过'
  } catch (cause) { error.value = cause instanceof Error ? cause.message : '汇率 API 测试失败' }
  finally { operation.value = null }
}
function showInvalidField() {
  notice.value = ''
  error.value = '请检查表单中标记的字段后再保存。'
}
onMounted(load)
</script>

<template>
  <form class="space-y-6" :aria-busy="operation !== null" @submit.prevent="save" @invalid.capture="showInvalidField" @input="notice = ''" @change="notice = ''">
    <header class="sticky top-[var(--workflow-mobile-nav-height,0px)] z-10 space-y-2 border-b border-accent-200 bg-white py-2 dark:border-dark-700 dark:bg-dark-900">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <h3 class="font-semibold">管理员共享设置</h3>
        <div class="flex flex-wrap items-center gap-2">
          <button type="button" class="btn btn-outline" :disabled="operation !== null" @click="load">重新读取</button>
          <button type="submit" class="btn btn-primary" :disabled="!settings || operation !== null">{{ operation === 'save' ? '保存中…' : '保存共享设置' }}</button>
        </div>
      </div>
      <p v-if="error" role="alert" class="text-sm text-red-600 dark:text-red-400">{{ error }}</p>
      <p v-if="notice" role="status" class="text-sm text-green-700 dark:text-green-400">{{ notice }}</p>
    </header>
    <p v-if="operation === 'load' && !settings" role="status">正在读取共享设置…</p>
    <fieldset v-if="settings" :disabled="operation !== null" class="space-y-6">
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
        <label class="block text-sm">管理员本人使用的 1688 方案
          <select v-model="settings.alibaba_api_application" class="input mt-2 w-full" aria-label="管理员本人使用的 1688 方案">
            <option v-for="application in applications" :key="application.key" :value="application.key">{{ application.title }}</option>
            <option value="onebound">万邦 1688 API</option>
          </select>
        </label>
        <p class="muted text-sm">仅决定管理员本人的商品采集、采购、支付和物流查询方案；普通用户固定使用万邦 1688 API。每套方案可在「1688 授权」中单独管理，切换时保留各方案的授权。</p>
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
        <p class="muted text-sm">API Key 和 API Secret 用于企业接入万邦。每位用户的 1688 买家授权单独保存。</p>
        <label class="block text-sm">API Key<input v-model="settings.onebound.api_key" maxlength="256" autocomplete="off" class="input mt-2 w-full" /></label>
        <label class="block text-sm">API Secret<input v-model="secrets.onebound" type="password" maxlength="4096" autocomplete="new-password" :placeholder="settings.onebound.secret_configured ? '已配置，填写可更换' : '尚未配置'" class="input mt-2 w-full" /></label>
        <label class="block text-sm">新买家基础额度（次）
          <input v-model.number="settings.onebound.initial_grant" type="number" min="0" max="100000" step="1" required class="input mt-2 w-full" />
        </label>
        <div
          v-if="oneboundQuota?.key?.disabled"
          class="flex flex-wrap items-center justify-between gap-2 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-200"
        >
          <span>{{ oneboundQuota.key.disabled_message || '万邦企业 Key 已暂停使用' }}</span>
          <button type="button" class="btn btn-secondary" :disabled="operation !== null" @click="restoreKey">
            {{ operation === 'restore' ? '恢复中…' : '已处理，恢复使用' }}
          </button>
        </div>
      </section>
      <section class="space-y-4 rounded-lg border border-accent-200 p-4 dark:border-dark-700" aria-label="Google Gemini Canvas 配置">
        <div class="flex flex-wrap items-center justify-between gap-3">
          <h4 class="font-semibold">Google Gemini Canvas 修图项目</h4>
          <button
            type="button"
            data-testid="reset-gemini-canvas-url"
            class="btn btn-outline py-1 text-xs"
            @click="settings.gemini_canvas_url = ''; notice = ''"
          >
            恢复默认地址
          </button>
        </div>
        <p class="muted text-sm">所有账号内置的 Gemini Canvas 修图通道共用此项目分享页地址。保存后全员自动生效，无需重新配置本机代理；留空保存将恢复默认地址。</p>
        <label class="block text-sm">修图项目地址
          <input
            v-model="settings.gemini_canvas_url"
            data-testid="managed-gemini-canvas-url"
            type="url"
            maxlength="2048"
            placeholder="https://gemini.google.com/share/..."
            class="input mt-2 w-full"
          />
        </label>
        <p class="muted text-xs">仅支持 https://gemini.google.com/share/... 或 https://gemini.google.com/app/... 地址。</p>
      </section>
      <section class="space-y-4 rounded-lg border border-accent-200 p-4 dark:border-dark-700">
        <h4 class="font-semibold">跨境巴士企业应用</h4>
        <label class="block text-sm">企业密钥<input v-model="secrets.crossborderbus" type="password" maxlength="4096" autocomplete="new-password" :placeholder="settings.crossborderbus_secret_configured ? '已配置，填写可更换' : '尚未配置'" class="input mt-2 w-full" /></label>
      </section>
    </fieldset>
  </form>
</template>
