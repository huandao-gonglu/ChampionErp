/** 页面定位快照；不包含商品正文、表单值或系统指令。 */
export const aiPageLabels = {
  dashboard: '仪表盘', research: '选品调研', collect: '采集', library: '商品库',
  drafts: '草稿箱', publish: '发布队列', mlUserProducts: 'ML User Products',
  pending: '待处理', auth: '平台授权与设置', logs: '发布日志', ai_work: '主对话',
  product_editor: '商品编辑', draft_editor: '草稿编辑',
} as const

export const aiSectionLabels = {
  text: '文本', images: '图片', category: '类目/公共属性',
  skus: 'SKU', pricing: '核价', precheck: '发布预检',
} as const

export interface AiPageContext {
  page: keyof typeof aiPageLabels
  section?: keyof typeof aiSectionLabels
  product_id?: string
  draft_id?: string
  platform?: string
  site?: string
  sku_id?: string
  attribute_id?: string
}

export function describeAiPageContext(context: AiPageContext | null): string {
  if (!context) return '当前页面没有可读取的背景'
  return [
    `当前页面：${aiPageLabels[context.page]}`,
    context.section && `当前区域：${aiSectionLabels[context.section]}`,
    context.product_id && `当前商品 ID：${context.product_id}`,
    context.draft_id && `当前草稿 ID：${context.draft_id}`,
    context.platform && `当前平台：${context.platform}`,
    context.site && `当前站点：${context.site}`,
    context.sku_id && `当前 SKU ID：${context.sku_id}`,
    context.attribute_id && `当前属性 ID：${context.attribute_id}`,
  ].filter(Boolean).join('\n')
}
