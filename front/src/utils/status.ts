export function workflowStatusLabel(status: string): string {
  const labels: Record<string, string> = {
    pending: '待处理',
    queued: '排队发布',
    running: '发布中',
    retrying: '重试中',
    publishing: '发布中',
    pending_confirmation: '已受理，待确认',
    collected: '已采集',
    claimed: '已认领',
    copy_ready: '文案完成',
    images_ready: '图片完成',
    priced: '已核价',
    category_ready: '类目完成',
    category_forbidden: '禁售类目',
    preparing: '准备中',
    needs_confirmation: '待确认',
    ready_to_publish: '可发布',
    not_ready: '预检未通过',
    published: '已发布',
    partial: '部分发布',
    publish_failed: '发布失败',
    failed: '发布失败',
    real_publish_failed: '发布失败',
    interrupted: '发布中断',
    outcome_unknown: '结果待确认',
    needs_attention: '需人工处理',
  }
  return labels[status] || status || '待处理'
}

export function statusBadgeClass(status: string): string {
  if (['done', 'copy_ready', 'images_ready', 'ready_to_publish', 'published', 'completed', 'success', 'finished', 'ready'].includes(status)) {
    return 'badge-success'
  }
  if (['active', 'running', 'retrying', 'queued', 'publishing', 'pending_confirmation', 'claimed', 'collected', 'partial'].includes(status)) {
    return 'badge-info'
  }
  if (['blocked', 'failed', 'publish_failed', 'error', 'real_publish_failed', 'not_ready', 'interrupted', 'outcome_unknown', 'needs_attention', 'category_forbidden'].includes(status)) {
    return 'badge-danger'
  }
  return 'badge-muted'
}
