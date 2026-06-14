export default {
  app: {
    title: 'Champion ERP 核心流程',
    subtitle: '真实接口驱动的前端流程面板',
  },
  routes: {
    workflow: {
      title: '跨境 ERP Web 工作台',
      description: '采集、商品库、编辑、图片文案、核价、预检、授权和日志。',
    },
    notFound: {
      title: '页面不存在',
      description: '请求的页面不存在或已迁移。',
    },
  },
  nav: {
    dashboard: { title: '仪表盘', subtitle: '流程总览' },
    collect: { title: '采集', subtitle: '链接、Cookie、浏览器标签' },
    library: { title: '商品库', subtitle: '本地商品母库' },
    drafts: { title: '草稿箱', subtitle: '平台草稿、继续编辑' },
    pricing: { title: '核价', subtitle: '成本、运费、利润' },
    category: { title: '发布预检', subtitle: '类目、属性、payload' },
    publish: { title: '发布队列', subtitle: '任务状态、日志' },
    mlItems: { title: 'ML已发布', subtitle: '远程商品、删除' },
    pending: { title: '待处理', subtitle: '未完成 / 失败商品' },
    auth: { title: '平台授权', subtitle: '授权、AI、汇率' },
    logs: { title: '发布日志', subtitle: '请求、响应、错误' },
  },
  pages: {
    drafts: {
      title: '草稿箱',
      description: '来自商品库的平台编辑稿，聚焦未发布完成的文案、图片、类目和发布预检。',
    },
    pricing: {
      title: '核价',
      description: '成本、运费、佣金、汇率和利润计算。',
    },
    precheck: {
      title: '发布预检',
      description: '类目搜索、必填属性填充、payload 预览、发布前校验。',
    },
    publish: {
      title: '发布队列',
      description: '发布队列、任务状态和运行日志。',
    },
    mlItems: {
      title: 'ML 已发布商品',
      description: '实时查看 Mercado Libre 账号商品，并支持通过 API 下架或结束发布。',
    },
    pending: {
      title: '待处理',
      description: '汇总采集、文案、图片、类目、预检或发布仍处于 pending / failed / not_ready / partial 的商品。',
    },
    logs: {
      title: '发布日志',
      description: '展示发布请求、响应、错误码和下一步处理建议。',
    },
  },
  settings: {
    uiLanguage: '界面语言',
  },
}
