import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from '@/App.vue'
import router from '@/router'
import i18n, { initI18n } from '@/i18n'
import { useAppStore } from '@/stores/app'
import '@/style.css'
import { useAccountStore } from '@/stores/account'
import { SESSION_EXPIRED_EVENT } from '@/utils/accountTransport'

function initThemeClass() {
  const savedTheme = localStorage.getItem('theme')
  const shouldUseDark = savedTheme === 'dark' || (!savedTheme && window.matchMedia('(prefers-color-scheme: dark)').matches)
  document.documentElement.classList.toggle('dark', shouldUseDark)
}

async function bootstrap() {
  initThemeClass()

  const app = createApp(App)
  const pinia = createPinia()

  app.use(pinia)
  const account = useAccountStore()
  let leaving = false
  window.addEventListener(SESSION_EXPIRED_EVENT, () => {
    if (leaving || !account.user) return
    leaving = true
    account.signedOut()
  })
  try {
    const channel = new BroadcastChannel('champion-account')
    channel.onmessage = () => { window.location.replace('/') }
  } catch { /* 不支持标签通信时，通过重新聚焦核对账号。 */ }
  window.addEventListener('focus', () => {
    const previous = account.user?.id
    void account.refresh().then(() => {
      if (previous && previous !== account.user?.id) account.signedOut()
    }).catch(() => {})
  })

  const appStore = useAppStore()
  appStore.applyTheme()

  await initI18n()

  app.use(router)
  app.use(i18n)

  await router.isReady()
  app.mount('#app')
}

void bootstrap()
