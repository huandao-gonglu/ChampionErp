import { createI18n } from 'vue-i18n'
import { DEFAULT_LOCALE, SUPPORTED_LOCALES, type SupportedLocale } from '@/constants/branding'
import zh from './locales/zh'
import en from './locales/en'

function detectLocale(): SupportedLocale {
  const saved = localStorage.getItem('locale')
  if (SUPPORTED_LOCALES.includes(saved as SupportedLocale)) return saved as SupportedLocale
  return navigator.language.toLowerCase().startsWith('zh') ? 'zh' : DEFAULT_LOCALE
}

export const i18n = createI18n({
  legacy: false,
  locale: detectLocale(),
  fallbackLocale: 'en',
  messages: { zh, en },
})

export async function initI18n() {
  const locale = detectLocale()
  i18n.global.locale.value = locale
  document.documentElement.lang = locale === 'zh' ? 'zh-CN' : 'en'
}

export function setLocale(locale: SupportedLocale) {
  localStorage.setItem('locale', locale)
  i18n.global.locale.value = locale
  document.documentElement.lang = locale === 'zh' ? 'zh-CN' : 'en'
}

export default i18n
