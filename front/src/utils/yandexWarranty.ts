export interface YandexGuaranteePeriod { timePeriod?: number | string; timeUnit?: string; comment?: string }
export const yandexWarrantyUnits = [['HOUR', '小时'], ['DAY', '天'], ['WEEK', '周'], ['MONTH', '个月'], ['YEAR', '年']]

export function yandexWarrantyError(period: YandexGuaranteePeriod, required = false): string {
  if (!required && !Object.values(period).some(value => value !== '')) return ''
  if (!/^[0-9]+$/.test(String(period.timePeriod ?? ''))) return '请填写非负整数的保修时长'
  if (!yandexWarrantyUnits.some(([unit]) => unit === period.timeUnit)) return '请选择保修期限单位'
  if ((period.comment || '').length > 500) return '保修说明不能超过 500 个字符'
  return ''
}

export function formatYandexWarranty(value: unknown): string {
  if (!value || typeof value !== 'object' || !('timePeriod' in value)) return '未填写'
  const period = value as YandexGuaranteePeriod
  const unit = yandexWarrantyUnits.find(([unit]) => unit === period.timeUnit)?.[1] || period.timeUnit || ''
  return `${period.timePeriod} ${unit}${period.comment ? `；${period.comment}` : ''}`
}
