import packageInfo from '../../package.json'

export const APP_NAME = packageInfo.productName
export { default as APP_LOGO } from '../../desktop/assets/icon.png'
export const APP_DESCRIPTION = '跨境 ERP Web 工作台'
export const APP_VERSION = `v${packageInfo.version}`
export const DEFAULT_LOCALE = 'zh'
export const SUPPORTED_LOCALES = ['zh', 'en'] as const

export type SupportedLocale = (typeof SUPPORTED_LOCALES)[number]
