export function workflowStatusLabel(status: string): string {
  const labels: Record<string, string> = {
    pending: '待处理',
    collected: '已采集',
    claimed: '已认领',
    copy_ready: '文案完成',
    images_ready: '图片完成',
    priced: '已核价',
    category_ready: '类目完成',
    ready_to_publish: '可发布',
    not_ready: '未通过',
    published: '已发布',
    failed: '失败',
  }
  return labels[status] || status || '待处理'
}

export function statusBadgeClass(status: string): string {
  if (['done', 'copy_ready', 'images_ready', 'ready_to_publish', 'published', 'completed', 'success', 'ready'].includes(status)) {
    return 'badge-success'
  }
  if (['active', 'running', 'queued', 'claimed', 'collected'].includes(status)) {
    return 'badge-info'
  }
  if (['blocked', 'failed', 'not_ready'].includes(status)) {
    return 'badge-danger'
  }
  return 'badge-muted'
}
