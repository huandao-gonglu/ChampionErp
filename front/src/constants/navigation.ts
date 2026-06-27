export interface WorkflowNavItem {
  key: string
  title: string
  subtitle: string
  titleKey?: string
  subtitleKey?: string
  icon: string
  disabled?: boolean
}

export const workflowNavItems: WorkflowNavItem[] = [
  { key: 'dashboard', title: '仪表盘', titleKey: 'nav.dashboard.title', subtitle: '流程总览', subtitleKey: 'nav.dashboard.subtitle', icon: '▦' },
  { key: 'research', title: '选品调研', titleKey: 'nav.research.title', subtitle: '市场需求、机会评分', subtitleKey: 'nav.research.subtitle', icon: '◈' },
  { key: 'collect', title: '采集', titleKey: 'nav.collect.title', subtitle: '链接、Cookie、浏览器标签', subtitleKey: 'nav.collect.subtitle', icon: '☷' },
  { key: 'library', title: '商品库', titleKey: 'nav.library.title', subtitle: '本地商品母库', subtitleKey: 'nav.library.subtitle', icon: '▱' },
  { key: 'drafts', title: '草稿箱', titleKey: 'nav.drafts.title', subtitle: '平台草稿、继续编辑', subtitleKey: 'nav.drafts.subtitle', icon: '▤' },
  { key: 'pricing', title: '核价', titleKey: 'nav.pricing.title', subtitle: '成本、运费、利润', subtitleKey: 'nav.pricing.subtitle', icon: '◷' },
  { key: 'category', title: '发布预检', titleKey: 'nav.category.title', subtitle: '类目、属性、payload', subtitleKey: 'nav.category.subtitle', icon: '◇' },
  { key: 'publish', title: '发布队列', titleKey: 'nav.publish.title', subtitle: '任务状态、日志', subtitleKey: 'nav.publish.subtitle', icon: '▣' },
  { key: 'mlItems', title: 'ML已发布', titleKey: 'nav.mlItems.title', subtitle: '远程商品、删除', subtitleKey: 'nav.mlItems.subtitle', icon: '▨' },
  { key: 'pending', title: '待处理', titleKey: 'nav.pending.title', subtitle: '未完成 / 失败商品', subtitleKey: 'nav.pending.subtitle', icon: '!' },
  { key: 'auth', title: '平台授权', titleKey: 'nav.auth.title', subtitle: '授权、AI、汇率', subtitleKey: 'nav.auth.subtitle', icon: '◎' },
  { key: 'logs', title: '发布日志', titleKey: 'nav.logs.title', subtitle: '请求、响应、错误', subtitleKey: 'nav.logs.subtitle', icon: '▥' },
]
