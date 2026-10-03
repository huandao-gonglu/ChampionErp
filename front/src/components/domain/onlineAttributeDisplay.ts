/** 展示平台属性原值，保留文本换行和枚举名称。 */
export function onlineAttributeText(attribute: Record<string, unknown>): string {
  if (Array.isArray(attribute.values)) return attribute.values.map(value => String(value.name ?? value.value ?? value.id ?? '')).join('、')
  return String(attribute.value_name ?? attribute.value ?? attribute.valueId ?? '')
}

export function onlineAttributeName(attribute: Record<string, unknown>): string {
  return String(attribute.name || attribute.parameterName || '名称暂未取得')
}
