<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useAccountStore } from '@/stores/account'
import RegistrationCode from '@/components/account/RegistrationCode.vue'
import { registerAccount } from '@/api/accounts'
import { APP_LOGO, APP_NAME } from '@/constants/branding'

const route = useRoute()
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
        auto.value = saved.available && saved.auto === true
        if (!password.value && !email.value) {
          email.value = saved.email
          password.value = saved.password
          remember.value = Boolean(saved.password)
        }
      }
    } catch { if (active) canRemember.value = false }
  }
}, { immediate: true })

async function submit() {
  if (busy.value) return
  error.value = ''
  if (registering.value && password.value !== confirmation.value) { error.value = '两次输入的密码不一致。'; return }
  busy.value = true
  try {
    if (registering.value) {
      await registerAccount({ email: email.value, display_name: name.value, password: password.value, email_code: emailCode.value })
      password.value = ''; confirmation.value = ''; registered.value = true
    } else await account.login(email.value, password.value, route.query.redirect,
      canRemember.value ? { remember: remember.value, auto: auto.value } : undefined)
  } catch (cause) { error.value = cause instanceof Error ? cause.message : '操作失败，请重试。' }
  finally { busy.value = false }
}
</script>

<template>
  <main class="flex min-h-screen items-center justify-center bg-accent-50 p-6 text-accent-950 dark:bg-dark-950 dark:text-white">
    <section class="w-full max-w-md rounded-2xl border border-accent-200 bg-white p-8 shadow-card dark:border-white/10 dark:bg-dark-900">
      <img :src="APP_LOGO" :alt="APP_NAME" class="mb-6 size-12" />
      <h1 class="text-2xl font-bold">{{ (registering ? '创建个人账号' : `登录 ${APP_NAME}`) }}</h1>
      <p class="mt-2 text-sm text-accent-500 dark:text-accent-300">管理你的商品、店铺和业务。</p>
      <p v-if="!account.initialized" role="status" class="mt-5 text-sm text-warning-700 dark:text-warning-300">账号服务暂未开放，请联系管理员。</p>
      <p v-if="route.query.newapi_sync === 'pending'" role="status" class="mt-5 text-sm text-warning-700 dark:text-warning-300">ERP 操作已完成；New API 会话或账号状态同步待重试。同步完成前，请勿假定 New API 已退出或停用。</p>
      <p v-if="account.error || error" role="alert" class="mt-5 text-sm text-danger-600 dark:text-danger-300">{{ error || account.error }}</p>
      <div v-if="registered" class="mt-6 space-y-4">
        <p role="status">注册成功，请使用邮箱和密码登录。</p>
        <RouterLink to="/login" class="btn btn-primary">登录</RouterLink>
      </div>
      <div v-else-if="registering && desktop" class="mt-6">
        <a href="/register" target="_blank" rel="noopener noreferrer" class="btn btn-primary">在浏览器中注册</a>
      </div>
      <form v-else class="mt-6 space-y-5" @submit.prevent="submit">
        <label v-if="registering" class="block text-sm font-semibold">
          显示名称
          <input v-model="name" name="display_name" autocomplete="name" required maxlength="80" class="input mt-2 w-full" :disabled="busy" />
        </label>
        <label class="block text-sm font-semibold">
          登录邮箱
          <input v-model="email" type="email" name="email" autocomplete="username" required maxlength="254" class="input mt-2 w-full" :disabled="busy" />
        </label>
        <label class="block text-sm font-semibold">
          密码
          <input v-model="password" type="password" name="password" :autocomplete="registering ? 'new-password' : 'current-password'" required :minlength="registering ? 15 : undefined" maxlength="128" class="input mt-2 w-full" :disabled="busy" />
          <span v-if="registering" class="mt-2 block text-xs font-normal text-accent-500 dark:text-accent-300">使用 15 至 128 个字符，可直接粘贴密码管理器生成的密码。</span>
        </label>
        <label v-if="registering" class="block text-sm font-semibold">
          确认密码
          <input v-model="confirmation" type="password" name="password_confirmation" autocomplete="new-password" required minlength="15" maxlength="128" class="input mt-2 w-full" :disabled="busy" />
        </label>
        <RegistrationCode v-if="registering" v-model="emailCode" :email="email" />
        <div v-else-if="canRemember" class="flex flex-wrap gap-x-5 gap-y-2 text-sm">
          <label><input v-model="remember" type="checkbox" /> 记住密码</label>
          <label><input v-model="auto" type="checkbox" /> 自动登录</label>
        </div>
        <button type="submit" class="btn btn-primary w-full" :disabled="busy || !account.initialized">{{ (busy ? '正在提交…' : (registering ? '注册' : '登录')) }}</button>
      </form>
      <p v-if="!registered" class="mt-6 text-center text-sm">
        {{ (registering ? '已有账号？' : '还没有账号？') }}
        <a v-if="desktop && !registering" href="/register" target="_blank" rel="noopener noreferrer" class="ml-2 font-semibold text-primary-600 dark:text-primary-300">在浏览器中注册</a>
        <RouterLink v-else :to="{ name: registering ? 'AccountLogin' : 'AccountRegister', query: route.query }" class="ml-2 font-semibold text-primary-600 dark:text-primary-300">{{ (registering ? '登录' : '注册') }}</RouterLink>
      </p>
      <p v-if="!registering" class="mt-5 text-xs text-accent-500 dark:text-accent-300">忘记密码请联系管理员重置。</p>
    </section>
  </main>
</template>
