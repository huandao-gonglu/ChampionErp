/** 与后端公开规则对应的即时提示；最终提交仍由后端校验，不截断用户输入。 */
export interface AttributeLimits {
  maxLength?: number | null
  minValue?: string | null
  maxValue?: string | null
  valueType?: string
}

function texts(value: unknown): string[] {
  if (value == null) return []
  if (Array.isArray(value)) return value.flatMap(texts)
  if (typeof value === 'object') {
    const record = value as Record<string, unknown>
    return texts(Array.isArray(record.values) ? record.values : record.value)
  }
  return [String(value).trim()]
}

export function attributeConstraintError(limits: AttributeLimits, value: unknown): string {
  const values = texts(value)
  for (const [index, text] of values.entries()) {
    if (!text) continue
    const prefix = values.length > 1 ? `第 ${index + 1} 个值：` : ''
    const length = Array.from(text).length
    if (limits.maxLength != null && length > limits.maxLength) {
      return `${prefix}当前 ${length} 个字符，超过上限 ${limits.maxLength}，需减少 ${length - limits.maxLength} 个字符`
    }
    if (['numeric', 'number', 'integer', 'float'].includes(limits.valueType || '')) {
      const normalized = text.replace(',', '.')
      const number = Number(normalized)
      if (!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(normalized)
        || !Number.isFinite(number) || (limits.valueType === 'integer' && !Number.isInteger(number))) return `${prefix}请填写有效的有限${limits.valueType === 'integer' ? '整数' : '数字'}`
      if (limits.minValue != null && number < Number(limits.minValue)) return `${prefix}当前值 ${text} 不能小于 ${limits.minValue}`
      if (limits.maxValue != null && number > Number(limits.maxValue)) return `${prefix}当前值 ${text} 不能大于 ${limits.maxValue}`
    }
  }
  return ''
}

export function attributeConstraintHint(limits: AttributeLimits, value: unknown): string {
  const hints: string[] = []
  if (limits.maxLength != null) {
    const lengths = texts(value).map(text => Array.from(text).length)
    hints.push(`每个值最多 ${limits.maxLength} 个字符；当前 ${lengths.length ? lengths.join('、') : 0} 个字符`)
  }
  if (limits.minValue != null) hints.push(`最小值 ${limits.minValue}`)
  if (limits.maxValue != null) hints.push(`最大值 ${limits.maxValue}`)
  return hints.join('；')
}
