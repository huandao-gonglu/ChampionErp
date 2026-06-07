export interface WorkflowNavItem {
  key: string
  title: string
  subtitle: string
  icon: string
  disabled?: boolean
}

export const workflowNavItems: WorkflowNavItem[] = [
  { key: 'dashboard', title: '仪表盘', subtitle: '流程总览', icon: '▦' },
  { key: 'collect', title: '采集', subtitle: '链接、Cookie、浏览器标签', icon: '☷' },
  { key: 'library', title: '商品库', subtitle: '本地商品母库', icon: '▱' },
  { key: 'pricing', title: '核价', subtitle: '成本、运费、利润', icon: '◷' },
  { key: 'category', title: '发布预检', subtitle: '类目、属性、payload', icon: '◇' },
  { key: 'publish', title: '发布队列', subtitle: '任务状态、日志', icon: '▣' },
  { key: 'pending', title: '待处理', subtitle: '未完成 / 失败商品', icon: '!' },
  { key: 'auth', title: '平台授权', subtitle: '授权、AI、汇率', icon: '◎' },
  { key: 'logs', title: '发布日志', subtitle: '请求、响应、错误', icon: '▥' },
]
