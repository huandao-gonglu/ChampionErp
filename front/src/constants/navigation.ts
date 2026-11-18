export interface WorkflowNavItem {
  key: string
  title: string
  subtitle: string
  disabled?: boolean
}

export const workflowNavItems: WorkflowNavItem[] = [
  { key: 'dashboard', title: '仪表盘', subtitle: '流程总览' },
  { key: 'orders', title: '订单中心', subtitle: '订单、采购与履约' },
  { key: 'research', title: '选品调研', subtitle: '市场需求、机会评分' },
  { key: 'collect', title: '采集', subtitle: '浏览器批量采集与 1688 API 采集' },
  { key: 'library', title: '采集箱', subtitle: '已采集的商品' },
  { key: 'drafts', title: '草稿箱', subtitle: '平台草稿、继续编辑' },
  { key: 'publish', title: '任务队列', subtitle: '任务状态、日志' },
  { key: 'onlineProducts', title: '在线商品', subtitle: '店铺商品、价格与库存' },
  { key: 'auth', title: '平台授权', subtitle: '授权、AI、汇率' },
  { key: 'systemSettings', title: '系统设置', subtitle: '应用偏好' },
]
