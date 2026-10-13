import { i18n } from '@/i18n'

/** 将内部操作标识转换为用户可读的业务名称。 */
export function describeAiTool(name: string): string {
  const key = `ai.tools.${name}`
  return i18n.global.te(key) ? i18n.global.t(key) : i18n.global.t('ai.operation')
}
