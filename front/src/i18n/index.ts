import { createI18n } from 'vue-i18n'
import { DEFAULT_UI_LOCALE, SUPPORTED_UI_LOCALES, type SupportedUiLocale, uiLocaleOption } from '@/constants/locales'
import zh from './locales/zh'
import en from './locales/en'

export function detectUiLocale(): SupportedUiLocale {
  const saved = localStorage.getItem('uiLocale') || localStorage.getItem('locale')
  if (SUPPORTED_UI_LOCALES.includes(saved as SupportedUiLocale)) return saved as SupportedUiLocale
  return navigator.language.toLowerCase().startsWith('zh') ? 'zh' : DEFAULT_UI_LOCALE
}

export const i18n = createI18n({
  legacy: false,
  locale: detectUiLocale(),
  fallbackLocale: 'en',
  messages: { zh, en },
})

export async function initI18n() {
  const locale = detectUiLocale()
  i18n.global.locale.value = locale
  document.documentElement.lang = uiLocaleOption(locale).htmlLang
}

export function setUiLocale(locale: SupportedUiLocale) {
  localStorage.setItem('uiLocale', locale)
  localStorage.setItem('locale', locale)
  i18n.global.locale.value = locale
  document.documentElement.lang = uiLocaleOption(locale).htmlLang
}

export default i18n
