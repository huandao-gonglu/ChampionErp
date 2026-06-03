import packageInfo from '../../package.json'

export const APP_NAME = 'Champion ERP Flow'
export const APP_SHORT_NAME = 'Champion ERP'
export const APP_DESCRIPTION = '跨境 ERP Web 工作台'
export const APP_VERSION = `v${packageInfo.version}`
export const DEFAULT_LOCALE = 'zh'
export const SUPPORTED_LOCALES = ['zh', 'en'] as const

export type SupportedLocale = (typeof SUPPORTED_LOCALES)[number]
