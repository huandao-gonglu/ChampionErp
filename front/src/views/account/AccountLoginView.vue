<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useAccountStore } from '@/stores/account'
import RegistrationCode from '@/components/account/RegistrationCode.vue'
import { registerAccount } from '@/api/accounts'
import { APP_LOGO, APP_NAME } from '@/constants/branding'

const route = useRoute()
const { t } = useI18n()
const account = useAccountStore()
const registering = computed(() => route.name === 'AccountRegister')
const email = ref('')
const name = ref('')
const emailCode = ref('')
const desktop = window.championDesktop
const remember = ref(false)
const auto = ref(false)
const canRemember = ref(false)
const password = ref('')
const confirmation = ref('')
const busy = ref(false)
const error = ref('')
const registered = ref(false)
watch(registering, () => { password.value = ''; confirmation.value = ''; error.value = ''; registered.value = false })

watch(registering, async (isRegistering, _, onCleanup) => {
  let active = true
  onCleanup(() => { active = false })
  emailCode.value = ''
  if (!isRegistering && desktop?.savedLogin) {
    try {
      const saved = await desktop.savedLogin()
      if (active) {
        canRemember.value = saved.available
        if (!password.value && !email.value && saved.password) {
          email.value = saved.email; password.value = saved.password; remember.value = true
        }
      }
    } catch { if (active) canRemember.value = false }
  }
}, { immediate: true })

async function submit() {
  if (busy.value) return
  error.value = ''
  if (registering.value && password.value !== confirmation.value) { error.value = t('account.passwordMismatch'); return }
  busy.value = true
  try {
    if (registering.value) {
      await registerAccount({ email: email.value, display_name: name.value, password: password.value, email_code: emailCode.value })
      password.value = ''; confirmation.value = ''; registered.value = true
    } else await account.login(email.value, password.value, route.query.redirect,
      canRemember.value ? { remember: remember.value, auto: auto.value } : undefined)
  } catch (cause) { error.value = cause instanceof Error ? cause.message : t('account.failed') }
  finally { busy.value = false }
}
</script>

<template>
  <main class="flex min-h-screen items-center justify-center bg-accent-50 p-6 text-accent-950 dark:bg-dark-950 dark:text-white">
    <section class="w-full max-w-md rounded-2xl border border-accent-200 bg-white p-8 shadow-card dark:border-white/10 dark:bg-dark-900">
      <img :src="APP_LOGO" :alt="APP_NAME" class="mb-6 size-12" />
      <h1 class="text-2xl font-bold">{{ t(registering ? 'account.registerTitle' : 'account.loginTitle') }}</h1>
      <p class="mt-2 text-sm text-accent-500 dark:text-accent-300">{{ t('account.intro') }}</p>
      <p v-if="!account.initialized" role="status" class="mt-5 text-sm text-warning-700 dark:text-warning-300">{{ t('account.uninitialized') }}</p>
      <p v-if="route.query.newapi_sync === 'pending'" role="status" class="mt-5 text-sm text-warning-700 dark:text-warning-300">{{ t('account.newapiPending') }}</p>
      <p v-if="account.error || error" role="alert" class="mt-5 text-sm text-danger-600 dark:text-danger-300">{{ error || account.error }}</p>
      <div v-if="registered" class="mt-6 space-y-4">
        <p role="status">{{ t('account.registered') }}</p>
        <RouterLink to="/login" class="btn btn-primary">{{ t('account.login') }}</RouterLink>
      </div>
      <div v-else-if="registering && desktop" class="mt-6">
        <a href="/register" target="_blank" rel="noopener noreferrer" class="btn btn-primary">{{ t('account.registerBrowser') }}</a>
      </div>
      <form v-else class="mt-6 space-y-5" @submit.prevent="submit">
        <label v-if="registering" class="block text-sm font-semibold">
          {{ t('account.name') }}
          <input v-model="name" name="display_name" autocomplete="name" required maxlength="80" class="input mt-2 w-full" :disabled="busy" />
        </label>
        <label class="block text-sm font-semibold">
          {{ t('account.email') }}
          <input v-model="email" type="email" name="email" autocomplete="username" required maxlength="254" class="input mt-2 w-full" :disabled="busy" />
        </label>
        <label class="block text-sm font-semibold">
          {{ t('account.password') }}
          <input v-model="password" type="password" name="password" :autocomplete="registering ? 'new-password' : 'current-password'" required :minlength="registering ? 15 : undefined" maxlength="128" class="input mt-2 w-full" :disabled="busy" />
          <span v-if="registering" class="mt-2 block text-xs font-normal text-accent-500 dark:text-accent-300">{{ t('account.passwordHint') }}</span>
        </label>
        <label v-if="registering" class="block text-sm font-semibold">
          {{ t('account.confirmPassword') }}
          <input v-model="confirmation" type="password" name="password_confirmation" autocomplete="new-password" required minlength="15" maxlength="128" class="input mt-2 w-full" :disabled="busy" />
        </label>
        <RegistrationCode v-if="registering" v-model="emailCode" :email="email" />
        <div v-else-if="canRemember" class="flex flex-wrap gap-x-5 gap-y-2 text-sm">
          <label><input v-model="remember" type="checkbox" /> {{ t('account.rememberPassword') }}</label>
          <label><input v-model="auto" type="checkbox" /> {{ t('account.autoLogin') }}</label>
        </div>
        <button type="submit" class="btn btn-primary w-full" :disabled="busy || !account.initialized">{{ t(busy ? 'account.submitting' : registering ? 'account.register' : 'account.login') }}</button>
      </form>
      <p v-if="!registered" class="mt-6 text-center text-sm">
        {{ t(registering ? 'account.haveAccount' : 'account.needAccount') }}
        <a v-if="desktop && !registering" href="/register" target="_blank" rel="noopener noreferrer" class="ml-2 font-semibold text-primary-600 dark:text-primary-300">{{ t('account.registerBrowser') }}</a>
        <RouterLink v-else :to="{ name: registering ? 'AccountLogin' : 'AccountRegister', query: route.query }" class="ml-2 font-semibold text-primary-600 dark:text-primary-300">{{ t(registering ? 'account.login' : 'account.register') }}</RouterLink>
      </p>
      <p v-if="!registering" class="mt-5 text-xs text-accent-500 dark:text-accent-300">{{ t('account.forgotPassword') }}</p>
    </section>
  </main>
</template>
