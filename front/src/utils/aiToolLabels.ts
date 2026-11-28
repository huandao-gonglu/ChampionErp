import type { BusinessWriteReceipt, CodeProgressSnapshot } from '@/types/aiWork'

const toolLabels: Record<string, string> = {
  user_agents_update: '保存 AI 自定义指令',
  request_user_input: '补充信息',
  search_tools: '查找可用能力',
  business_data_catalog: '查看业务数据目录',
  business_data_read: '读取业务数据',
  run_code: '处理业务',
  order_detail_read: '查看订单详情',
  orders_query: '查询订单',
  orders_summary_read: '汇总订单',
  orders_sync: '同步订单',
  order_notification_update: '更新订单通知',
  order_source_select: '选择采购货源',
  order_purchase_record: '记录采购信息',
  order_purchase_cancel: '取消采购记录',
  order_purchase_query: '查询采购记录',
  order_purchase_sync: '同步采购进度',
  order_address_note_read: '查看地址备注',
  order_address_note_update: '更新地址备注',
  order_buyer_chat_read: '查看买家消息',
  order_buyer_chat_send: '联系买家',
  order_alibaba_options: '查询采购选项',
  order_alibaba_batch_options: '批量查询采购选项',
  order_alibaba_addresses: '查询采购收货地址',
  order_alibaba_parse_address: '识别收货地址',
  order_alibaba_preview: '预览采购单',
  order_alibaba_batch_preview: '批量预览采购单',
  order_alibaba_create: '创建采购单',
  order_alibaba_payment_status: '查询付款状态',
  order_alibaba_pay: '支付采购单',
  order_alibaba_reconcile: '核对采购单',
  order_alibaba_cashier: '打开采购收银台',
  order_receiving_warehouse_read: '查看收货仓库',
  order_receiving_warehouse_update: '更新收货仓库',
  order_fulfillment_read: '查看物流履约',
  order_fulfillment_services: '查询物流服务',
  order_fulfillment_update: '处理物流履约',
  online_products_read: '查询在线商品',
  online_products_attributes: '查看在线商品属性',
  online_products_job_read: '查看在线商品任务',
  online_products_refresh_detail: '刷新在线商品详情',
  online_products_refresh_status: '查看商品同步进度',
  online_products_close: '结束待核对操作',
  online_products_change: '修改在线商品',
  online_products_changes_apply: '批量修改在线商品',
  online_products_sync: '同步在线商品',
  online_products_reconcile: '核对在线商品',
  online_products_retry: '重试商品操作',
  conversation_facts_query: '查询相关对话记录',
  product_read: '查看商品',
  product_attributes_update: '更新商品属性',
  product_images_prepare: '准备商品图片',
  product_save: '保存商品',
  product_delete: '删除商品',
  product_profile_patch: '修改商品信息',
  category_match: '匹配商品类目',
  category_match_batch: '批量匹配商品类目',
  category_search: '搜索类目',
  category_attributes_query: '查询类目属性',
  category_attributes_query_batch: '批量查询类目属性',
  category_attribute_values_query: '查询属性选项',
  category_precheck: '检查类目信息',
  draft_prepare_for_market: '准备平台草稿',
  product_publish_prepare: '准备商品发布',
  product_publish_validate: '检查发布条件',
  product_publish_validate_batch: '批量检查发布条件',
  product_publish_request: '发布商品',
  drafts_query: '查询草稿',
  draft_read: '查看草稿',
  draft_save: '保存草稿',
  draft_delete: '删除草稿',
  draft_duplicate: '复制草稿',
  draft_attributes_read: '查看草稿属性',
  draft_attributes_read_batch: '批量查看草稿属性',
  draft_sku_attributes_update: '更新规格属性',
  draft_sku_variants_check: '核对规格差异',
  draft_sku_variants_check_batch: '批量核对规格差异',
  draft_sku_selection_update: '选择商品规格',
  draft_package_estimates_preview: '比较尺寸区间运费',
  draft_package_estimates_confirm: '确认包装尺寸估算',
  draft_package_estimates_apply: '应用包装估算',
  product_sku_facts_update: '更新商品 SKU 资料',
  draft_changes_apply: '保存草稿修改',
  copy_generate: '生成商品文案',
  copy_generate_batch: '批量生成文案',
  image_prompts_generate: '准备图片创作描述',
  text_translate: '翻译文本',
  image_pool_upload: '上传图片',
  image_pool_save: '保存图片',
  image_pool_action: '管理商品图片',
  image_pool_sync_generated: '同步生成图片',
  product_image_read: '读取商品图片',
  chat_images_save_to_product: '保存对话图片到商品',
  image_translate: '翻译图片',
  image_edit: '编辑图片',
  draft_images_prepare: '准备草稿套图',
  draft_images_wait: '等待草稿套图',
  products_index_query: '查询采集箱',
  platform_orders_query: '查询平台订单',
  publish_logs_query: '查询发布记录',
  publish_jobs_query: '查询发布任务',
  publish_job_status_query: '查看发布进度',
  upc_assign: '分配商品条码',
  upc_import: '导入商品条码',
  draft_pricing_preview: '预览核价结果',
  draft_pricing_preview_batch: '批量预览核价结果',
  draft_pricing_apply: '应用核价结果',
  draft_pricing_apply_batch: '批量应用核价结果',
  store_auth_checklist: '查看店铺授权要求',
  store_auth_check: '检查店铺授权',
  source_collect: '采集商品',
  collection_links_add: '添加采集链接',
  collection_links_read: '查看采集链接',
  collect_batch: '批量采集商品',
  collect_from_browser_tab: '从浏览器采集商品',
  collect_1688: '采集 1688 商品',
  collect_1688_clean: '整理 1688 商品',
  claim_products: '认领商品',
  research_hot_products_search: '搜索热门商品',
  research_run_status_query: '查看选品进度',
  inspect_source_facts: '查看货源资料',
}

const WRITE_TOOL_NAMES = new Set([
  'user_agents_update',
  'orders_sync',
  'order_notification_update',
  'order_source_select',
  'order_purchase_record',
  'order_purchase_cancel',
  'order_purchase_sync',
  'order_address_note_update',
  'order_buyer_chat_send',
  'order_alibaba_create',
  'order_alibaba_pay',
  'order_receiving_warehouse_update',
  'order_fulfillment_update',
  'online_products_close',
  'online_products_change',
  'online_products_changes_apply',
  'online_products_sync',
  'online_products_retry',
  'product_attributes_update',
  'product_images_prepare',
  'product_save',
  'product_delete',
  'product_profile_patch',
  'category_match',
  'category_match_batch',
  'draft_prepare_for_market',
  'product_publish_prepare',
  'product_publish_request',
  'draft_save',
  'draft_delete',
  'draft_duplicate',
  'draft_sku_attributes_update',
  'draft_sku_selection_update',
  'draft_package_estimates_confirm',
  'draft_package_estimates_apply',
  'product_sku_facts_update',
  'draft_changes_apply',
  'copy_generate',
  'copy_generate_batch',
  'image_prompts_generate',
  'image_pool_upload',
  'image_pool_save',
  'image_pool_action',
  'image_pool_sync_generated',
  'chat_images_save_to_product',
  'image_translate',
  'image_edit',
  'draft_images_prepare',
  'upc_assign',
  'upc_import',
  'draft_pricing_apply',
  'draft_pricing_apply_batch',
  'source_collect',
  'collection_links_add',
  'collect_batch',
  'collect_from_browser_tab',
  'collect_1688',
  'collect_1688_clean',
  'claim_products',
])

export type AiToolCategory = 'read' | 'write' | 'compute'

/** 将内部操作标识转换为用户可读的业务名称。 */
export function describeAiTool(name: string): string {
  return toolLabels[name] || '处理操作'
}

const CALL_PATTERN = /\b([a-z][a-z0-9_]{2,})\s*\(/g

function extractCodeToolNames(input: unknown): string[] {
  if (!input || typeof input !== 'object') return []
  const code = (input as { code?: unknown }).code
  if (typeof code !== 'string' || !code.trim()) return []
  const found: string[] = []
  const seen = new Set<string>()
  for (const match of code.matchAll(CALL_PATTERN)) {
    const name = match[1]
    if (name === 'run_code' || seen.has(name) || !(name in toolLabels)) continue
    seen.add(name)
    found.push(name)
  }
  return found
}

/** 提取 run_code 关联的真实子工具名（优先实时进度、其次写回执、最后脚本静态调用）。 */
export function extractRunCodeToolNames(
  input: unknown,
  progress?: CodeProgressSnapshot,
  writeReceipts?: BusinessWriteReceipt[],
  toolCallId?: string,
): string[] {
  const names: string[] = []
  const seen = new Set<string>()
  const pushName = (name: string) => {
    if (!name || name === 'run_code' || seen.has(name)) return
    seen.add(name)
    names.push(name)
  }
  if (progress?.tools?.length) {
    for (const item of progress.tools) pushName(item.name)
  }
  if (!names.length && toolCallId && writeReceipts?.length) {
    const prefix = `${toolCallId}__`
    for (const receipt of writeReceipts) {
      if (receipt.tool_call_id.startsWith(prefix)) pushName(receipt.tool_name)
    }
  }
  if (!names.length) {
    for (const name of extractCodeToolNames(input)) pushName(name)
  }
  return names
}

/** 区分只读探索、业务写入与纯计算核对，用于读写视觉分层。 */
export function classifyAiToolCategory(
  toolName: string,
  input?: unknown,
  progress?: CodeProgressSnapshot,
  writeReceipts?: BusinessWriteReceipt[],
  toolCallId?: string,
): AiToolCategory {
  if (toolName === 'run_code') {
    const subTools = extractRunCodeToolNames(input, progress, writeReceipts, toolCallId)
    if (!subTools.length) return 'compute'
    return subTools.some(name => WRITE_TOOL_NAMES.has(name)) ? 'write' : 'read'
  }
  return WRITE_TOOL_NAMES.has(toolName) ? 'write' : 'read'
}

/**
 * 为 run_code 生成去技术化的业务动作标题（仅展示用途，不反解析业务结果）。
 * 直接展示真实业务动作名，不再暴露“批量处理 ·”前缀。
 */
export function describeRunCodeAction(
  input: unknown,
  progress?: CodeProgressSnapshot,
  writeReceipts?: BusinessWriteReceipt[],
  toolCallId?: string,
): string {
  const names = extractRunCodeToolNames(input, progress, writeReceipts, toolCallId)
  const labels: string[] = []
  for (const name of names) {
    const label = toolLabels[name]
    if (label && !labels.includes(label)) labels.push(label)
  }
  if (!labels.length) {
    const code = input && typeof input === 'object' ? (input as { code?: unknown }).code : undefined
    return typeof code === 'string' && code.trim() ? '核对与整理数据' : toolLabels.run_code
  }
  const shown = labels.slice(0, 2).join('、')
  return labels.length > 2 ? `${shown}等 ${labels.length} 项` : shown
}

/** 生成进行中步骤的统一自然动词文案，消除“执行中 / 处理中 / 正在准备”并存的割裂感。 */
export function formatActiveToolLabel(label: string): string {
  const clean = label.trim()
  if (!clean) return '正在处理'
  if (clean.startsWith('正在')) return clean
  return `正在${clean}`
}

/** 将瞬态 data-code-progress 格式化为紧凑的单行计数进度（如 11/20 · 1 项异常）。 */
export function formatCodeProgressCompact(progress?: CodeProgressSnapshot): string {
  if (!progress || progress.started <= 0) return ''
  const failSuffix = progress.failed > 0 ? ` · ${progress.failed} 项异常` : ''
  if (progress.active === 0) {
    return `${progress.completed}/${progress.started}${failSuffix} · 整理结果`
  }
  return `${progress.completed}/${progress.started}${failSuffix}`
}

/** 将瞬态 data-code-progress 格式化为可读的子步骤实时进度。 */
export function formatCodeProgress(progress?: CodeProgressSnapshot): string {
  if (!progress || progress.started <= 0) return ''
  const currentTool = progress.tools.find(item => item.name === progress.tool_name)
    ?? progress.tools.at(-1)
  const label = describeAiTool(currentTool?.name || progress.tool_name)
  const failSuffix = progress.failed > 0 ? `，${progress.failed} 项异常` : ''
  if (progress.active === 0) {
    return `已完成 ${progress.completed}/${progress.started} 项${label}${failSuffix}，正在整理结果…`
  }
  if (progress.tools.length > 1 && currentTool) {
    return `正在${label}：${currentTool.completed}/${currentTool.started}（本步共 ${progress.completed}/${progress.started}${failSuffix}）`
  }
  return `正在${label}：已完成 ${progress.completed}/${progress.started}${failSuffix}`
}
