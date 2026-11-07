<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { registrationConfig, sendRegistrationCode } from '@/api/accounts'

interface Turnstile {
  render: (element: HTMLElement, options: Record<string, unknown>) => string
  reset: (id: string) => void
  remove: (id: string) => void
}
const props = defineProps<{ email: string }>()
const code = defineModel<string>({ required: true })
const { t } = useI18n()
const widget = ref<HTMLElement>()
const token = ref('')
const sending = ref(false)
const remaining = ref(0)
const message = ref('')
const error = ref('')
let timer: ReturnType<typeof setInterval>
let api: Turnstile | undefined
let widgetId: string | undefined
let script: HTMLScriptElement | undefined
let mounted = true

async function loadWidget() {
  if (widgetId !== undefined) return
  error.value = ''
  try {
    const { site_key: sitekey } = await registrationConfig()
    if (!mounted) return
    if (!sitekey) throw new Error(t('account.verificationUnavailable'))
    const host = window as Window & { turnstile?: Turnstile }
    if (!host.turnstile) {
      script?.remove()
      script = document.createElement('script')
      script.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit'
      const loaded = new Promise<void>((resolve, reject) => {
        script!.onload = () => resolve()
        script!.onerror = () => reject(new Error(t('account.verificationUnavailable')))
      })
      document.head.appendChild(script)
      await loaded
    }
    if (!mounted || !widget.value) return
    api = host.turnstile
    if (!api) throw new Error(t('account.verificationUnavailable'))
    widgetId = api.render(widget.value, { sitekey, action: 'register_email', callback: (value: string) => { token.value = value },
      'expired-callback': () => { token.value = '' }, 'error-callback': () => { token.value = '' } })
  } catch (cause) { if (mounted) error.value = cause instanceof Error ? cause.message : t('account.failed') }
}

async function send() {
  if (sending.value || !token.value || remaining.value) return
  const recipient = props.email.trim()
  error.value = ''; message.value = ''; sending.value = true
  try {
    await sendRegistrationCode(recipient, token.value)
    if (!mounted) return
    message.value = t('account.codeSent', { email: recipient })
    remaining.value = 60
    timer = setInterval(() => { if (--remaining.value <= 0) clearInterval(timer) }, 1000)
  } catch (cause) { error.value = cause instanceof Error ? cause.message : t('account.failed') }
  finally {
    sending.value = false; token.value = ''
    if (mounted && widgetId !== undefined) api?.reset(widgetId)
  }
}
onMounted(loadWidget)
onUnmounted(() => {
  mounted = false; clearInterval(timer)
  if (widgetId !== undefined) api?.remove(widgetId)
  script?.remove()
})
</script>
<template>
  <div class="space-y-2">
    <div ref="widget"></div>
    <label class="block text-sm font-semibold">
      {{ t('account.emailCode') }}
      <input v-model="code" name="email_code" autocomplete="one-time-code" inputmode="numeric" pattern="[0-9]{6}" maxlength="6" required class="input mt-2 w-full" />
    </label>
    <button type="button" class="btn btn-outline" :disabled="!token || !email.trim() || sending || remaining > 0" @click="send">
      {{ remaining ? t('account.resendIn', { seconds: remaining }) : t('account.sendCode') }}
    </button>
    <p v-if="message" role="status" class="text-sm">{{ message }}</p>
    <p v-if="error" role="alert" class="text-sm text-danger-600">
      {{ error }}
      <button v-if="widgetId === undefined" type="button" class="underline" @click="loadWidget">{{ t('account.retryVerification') }}</button>
    </p>
  </div>
</template>
