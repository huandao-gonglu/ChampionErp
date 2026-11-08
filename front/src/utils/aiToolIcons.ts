import type { Component } from 'vue'
import {
  PhArrowsClockwise, PhBarcode, PhBookOpen, PhCheckSquare, PhCode,
  PhCopy, PhCreditCard, PhGlobe, PhImage, PhMagnifyingGlass,
  PhPackage, PhPaperPlaneTilt, PhPencilSimple, PhPlusCircle,
  PhTranslate, PhTrash, PhUploadSimple, PhWrench,
} from '@phosphor-icons/vue'

/** 图标只描述操作类型，不表示执行状态，也不从工具参数推断实际业务。 */
const groups: [Component, string[]][] = [
  [PhCode, ['run_code']],
  [PhBookOpen, [
    'order_detail_read', 'orders_summary_read', 'order_address_note_read',
    'order_buyer_chat_read', 'order_receiving_warehouse_read', 'order_fulfillment_read',
    'product_read', 'draft_read', 'draft_attributes_read', 'inspect_source_facts',
    'store_auth_checklist',
  ]],
  [PhMagnifyingGlass, [
    'order_purchase_query', 'order_alibaba_options', 'order_alibaba_batch_options',
    'order_alibaba_addresses', 'order_alibaba_payment_status', 'order_fulfillment_services',
    'online_products_read', 'online_products_refresh_status', 'conversation_facts_query',
    'category_search', 'category_attributes_query', 'category_attribute_values_query',
    'drafts_query', 'products_index_query', 'platform_orders_query', 'publish_logs_query',
    'publish_jobs_query', 'publish_job_status_query', 'research_hot_products_search',
    'research_run_status_query', 'order_alibaba_preview', 'order_alibaba_batch_preview',
    'draft_pricing_preview',
  ]],
  [PhPencilSimple, [
    'order_notification_update', 'order_source_select', 'order_purchase_record',
    'order_address_note_update', 'order_receiving_warehouse_update', 'order_fulfillment_update',
    'online_products_change', 'product_attributes_update', 'product_save', 'product_profile_patch',
    'draft_save', 'draft_sku_attributes_update', 'draft_sku_selection_update',
    'product_sku_facts_update', 'draft_changes_apply', 'draft_pricing_apply',
    'copy_generate', 'copy_generate_batch',
  ]],
  [PhArrowsClockwise, [
    'orders_sync', 'order_purchase_sync', 'online_products_sync', 'online_products_retry',
    'image_pool_sync_generated',
  ]],
  [PhCheckSquare, [
    'order_alibaba_reconcile', 'online_products_reconcile', 'category_match',
    'category_precheck', 'product_publish_validate', 'store_auth_check',
  ]],
  [PhTrash, ['product_delete', 'draft_delete', 'order_purchase_cancel']],
  [PhPaperPlaneTilt, ['order_buyer_chat_send', 'product_publish_request']],
  [PhCreditCard, ['order_alibaba_pay', 'order_alibaba_cashier']],
  [PhPlusCircle, ['order_alibaba_create']],
  [PhCopy, ['draft_duplicate']],
  [PhTranslate, ['text_translate', 'order_alibaba_parse_address', 'image_translate']],
  [PhImage, ['product_image_read', 'product_images_prepare', 'image_prompts_generate', 'image_pool_save', 'image_pool_action', 'image_edit']],
  [PhUploadSimple, ['chat_images_save_to_product', 'image_pool_upload', 'upc_import']],
  [PhBarcode, ['upc_assign']],
  [PhPackage, ['draft_prepare_for_market', 'product_publish_prepare', 'claim_products']],
  [PhGlobe, ['source_collect', 'collect_batch', 'collect_from_browser_tab', 'collect_1688', 'collect_1688_clean']],
]

const icons = new Map(groups.flatMap(([icon, names]) => names.map(name => [name, icon] as const)))

export function aiToolIcon(name: string): Component {
  return icons.get(name) ?? PhWrench
}
