<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useAccountStore } from '@/stores/account'
import { authorizeNewAPI, fetchNewAPIInfo, previewNewAPI } from '@/api/accounts'

const { t } = useI18n()
const route = useRoute()
const account = useAccountStore()
const info = ref<Awaited<ReturnType<typeof fetchNewAPIInfo>> | null>(null)
const code = ref(typeof route.query.newapi_code === 'string' ? route.query.newapi_code : '')
const password = ref('')
const preview = ref<Awaited<ReturnType<typeof previewNewAPI>> | null>(null)
const busy = ref(false)
const error = ref('')
const approved = ref(false)
watch(code, () => { preview.value = null; password.value = ''; approved.value = false })

async function perform(action: () => Promise<void>) {
  if (busy.value) return
  busy.value = true; error.value = ''
  try { await action() } catch (cause) { error.value = cause instanceof Error ? cause.message : t('account.failed') }
  finally { busy.value = false; password.value = '' }
}
onMounted(() => perform(async () => { info.value = await fetchNewAPIInfo() }))
async function inspect() {
  await perform(async () => { preview.value = await previewNewAPI(code.value.trim().toUpperCase()) })
}
async function approve() {
  if (!preview.value) return
  const approvedCode = preview.value.code
  await perform(async () => {
    await authorizeNewAPI({ code: approvedCode, ...(preview.value?.target === 'proof' ? { password: password.value } : {}) })
    approved.value = true; preview.value = null
  })
}
</script>

<template>
  <section class="space-y-4 rounded-2xl border border-accent-200 bg-white p-6 dark:border-white/10 dark:bg-dark-900">
    <h2 class="text-lg font-bold">{{ t('account.newapi') }}</h2>
    <p v-if="error" role="alert" class="text-sm text-danger-600 dark:text-danger-300">{{ error }}</p>
    <p v-if="info && !info.configured" class="text-sm">{{ t('account.newapiNotConfigured') }}</p>
    <template v-if="info?.configured">
      <p class="text-sm">{{ t('account.newapiIntro') }}</p>
      <a :href="info.url" target="_blank" rel="noopener noreferrer" class="btn btn-secondary">{{ t('account.newapiOpen') }}</a>
      <p v-if="info.sync_pending" role="status" class="text-sm text-warning-700 dark:text-warning-300">{{ t('account.newapiPending') }}</p>
      <form class="space-y-4" @submit.prevent="inspect">
        <label class="block text-sm font-semibold">{{ t('account.newapiCode') }}<input v-model="code" class="input mt-2 w-full font-mono uppercase" autocomplete="off" minlength="12" maxlength="12" required :disabled="busy" /></label>
        <button type="submit" class="btn btn-secondary" :disabled="busy">{{ t('account.newapiInspect') }}</button>
      </form>
      <form v-if="preview" class="space-y-4" @submit.prevent="approve">
        <p class="text-sm">{{ t('account.newapiConfirm', { email: account.user?.email, code: preview.code }) }}</p>
        <p class="text-sm font-semibold">{{ t(`account.newapiTarget_${preview.target}`) }}</p>
        <label v-if="preview.target === 'proof'" class="block text-sm font-semibold">{{ t('account.password') }}<input v-model="password" type="password" autocomplete="current-password" required maxlength="128" class="input mt-2 w-full" :disabled="busy" /></label>
        <button type="submit" class="btn btn-primary" :disabled="busy">{{ t('account.newapiApprove') }}</button>
      </form>
      <p v-if="approved" role="status" class="text-sm">{{ t('account.newapiApproved') }}</p>
    </template>
  </section>
</template>
