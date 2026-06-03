import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from '@/App.vue'
import router from '@/router'
import i18n, { initI18n } from '@/i18n'
import { useAppStore } from '@/stores/app'
import '@/style.css'

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

  const appStore = useAppStore()
  appStore.applyTheme()

  await initI18n()

  app.use(router)
  app.use(i18n)

  await router.isReady()
  app.mount('#app')
}

void bootstrap()
